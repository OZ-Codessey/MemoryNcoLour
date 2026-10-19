"""
================================================================================
MaC — 그림 생성 (Gemini 이미지 모델, 그림 단계에서만 Gemini 를 쓴다)
================================================================================
입력: paint_prompt.build_paint_request(...) 가 만든 요청 + references/ 의 레퍼런스 그림
출력: PIL.Image (RGB)

REST 로 직접 보낸다 (POST /v1beta/interactions — 구글 공식 문서의 REST 예제와 같은 형식).
  - google-genai SDK 를 쓰지 않는다: SDK 는 429·5xx 를 '조용히' 최대 4번 다시 보내서 무슨 일인지 안 보였고,
    시간 제한도 실제 연결을 끊지 못했다. requests 의 timeout 은 연결 자체를 끊는다.
  - Vercel 함수 용량도 줄어든다 (requests 는 원래 requirements.txt 에 있음).

보내는 내용:
    input = [레퍼런스 안내 글] + [레퍼런스 그림들] + [고정 스타일 + 이번 요청 글(재료·경계·에너지·분위기·구도·팔레트)]
    response_format = image/jpeg, aspect_ratio, 1K
    generation_config.thinking_level (선택)
  ※ system_instruction 은 쓰지 않는다. 2026-10-20 같은 시각 비교 실험(ab·ladder)에서
    system_instruction 을 붙인 요청은 혼잡 시 1/8 만 성공, 스타일을 input 글 앞에 합친 요청은 2/2·5/8 성공.

환경변수
  GEMINI_API_KEY       필수
  GEMINI_IMAGE_MODEL   기본 gemini-nano-banana-2.1  (비교용: gemini-3.1-flash-image, gemini-3-pro-image)
  GEMINI_IMAGE_FALLBACKS  첫 모델이 혼잡(503)·한도(429)일 때 차례로 넘어갈 모델들, 쉼표로.
                          기본 gemini-3.1-flash-image (이전 세대 Nano Banana 2). 비우려면 'none'

재시도: 503·429·연결 오류만, 최대 retries 번(기본 3), 5초 → 10초 → 20초 간격. 매 시도를 log(…) 로 알린다.
키는 로그에 남기지 않는다.
"""
from __future__ import annotations

import base64
import io
import os
import time
from pathlib import Path

import requests
from PIL import Image

try:
    from services.claude_client import api_key, load_env_file
except ImportError:                                   # 패키지 밖 단독 실행
    from claude_client import api_key, load_env_file

ROOT = Path(__file__).resolve().parents[1]
REF_DIR = ROOT / "references"
URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
load_env_file()
DEFAULT_MODEL = os.getenv("GEMINI_IMAGE_MODEL") or "gemini-nano-banana-2.1"
_fb = (os.getenv("GEMINI_IMAGE_FALLBACKS") or "gemini-3.1-flash-image").strip()
DEFAULT_FALLBACKS = [] if _fb.lower() == "none" else [m.strip() for m in _fb.split(",") if m.strip()]


class PaintError(RuntimeError):
    pass


def load_references(ref_dir: Path = REF_DIR, limit: int = 3) -> list[bytes]:
    files = sorted(Path(ref_dir).glob("ref_*.jpg"))[:limit]
    return [f.read_bytes() for f in files]


def build_body(req: dict, refs: list[bytes], model: str, aspect: str = "4:5", thinking: str | None = None,
               avoid: bytes | None = None) -> dict:
    """avoid: 직전 그림(JPEG). 주면 '이것과 다르게'라는 안내와 함께 첨부한다."""
    parts = []
    if refs:
        parts.append({"type": "text", "text": req["reference_intro"]})
        parts += [{"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(b).decode()} for b in refs]
    if avoid:
        parts.append({"type": "text", "text": req.get("avoid_intro", "Paint something clearly different from this image.")})
        parts.append({"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(avoid).decode()})
    parts.append({"type": "text", "text": req["system"] + "\n\n" + req["request"]})
    body = {"model": model, "input": parts,
            "response_format": {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": aspect, "image_size": "1K"}}
    if thinking:
        body["generation_config"] = {"thinking_level": thinking}     # minimal | medium(기본) | high
    return body


def find_image(obj, skip_thoughts: bool = True):
    """응답 JSON 어디에 있든 {"type": "image", "data": ...} 중 마지막 것(= 최종 그림)을 찾는다.
    (thinking 단계의 중간 그림은 thought 요약 안에 있고, 최종 그림이 뒤에 온다)"""
    found = None
    if isinstance(obj, dict):
        if obj.get("type") == "image" and obj.get("data"):
            found = obj
        for k, v in obj.items():
            if skip_thoughts and k == "summary":     # thought 단계의 중간 그림은 건너뛴다
                continue
            found = find_image(v, skip_thoughts) or found
    elif isinstance(obj, list):
        for v in obj:
            found = find_image(v, skip_thoughts) or found
    return found


def find_text(obj) -> str:
    out = []
    if isinstance(obj, dict):
        if obj.get("type") == "text" and isinstance(obj.get("text"), str):
            out.append(obj["text"])
        for v in obj.values():
            out.append(find_text(v))
    elif isinstance(obj, list):
        out += [find_text(v) for v in obj]
    return " ".join(x for x in out if x)


def _error_message(r) -> str:
    try:
        return r.json().get("error", {}).get("message", "")[:200]
    except Exception:
        return r.text[:200]


class _Busy(PaintError):
    """혼잡(503)·한도(429) — 다음 모델로 넘어갈 수 있는 실패"""


def paint(req: dict, refs: list[bytes] | None = None, *, model: str | None = None, aspect: str = "4:5",
          thinking: str | None = None, timeout: float = 120.0, retries: int = 3, log=print,
          session=None, fallbacks: list[str] | None = None, avoid: bytes | None = None) -> tuple[Image.Image, dict]:
    """그림 한 장. 반환: (이미지, 정보 {model, seconds, attempts, refs, thinking, tried})
    첫 모델이 혼잡(503)·한도(429)이면 fallbacks(기본 GEMINI_IMAGE_FALLBACKS) 모델로 차례로 넘어간다.
    timeout: 요청 1번의 응답 대기 한도(초) — 넘기면 연결을 끊는다.
    retries: 모델마다 503·429·연결 오류일 때 다시 보낼 횟수 (기본 3번)."""
    first = (model or DEFAULT_MODEL).strip()
    chain = [first] + [m for m in (DEFAULT_FALLBACKS if fallbacks is None else fallbacks) if m != first]
    tried, t0 = [], time.time()
    for i, m in enumerate(chain):
        try:
            # 다음 모델은 생각 단계 옵션을 빼고 보낸다 (모델마다 받는 thinking_level 값이 다름)
            img, info = _paint_one(req, refs, m, aspect, thinking if i == 0 else None, timeout, retries, log, session, avoid)
            info.update(tried=tried + [m], seconds=round(time.time() - t0, 1))
            return img, info
        except _Busy as e:
            tried.append(m)
            if i + 1 < len(chain):
                log(f"  [gemini] {m} 혼잡 → {chain[i + 1]} 로 넘어감")
                continue
            raise PaintError(f"{e} (시도한 모델: {', '.join(tried)})") from e
    raise PaintError("unreachable")


def _paint_one(req, refs, model, aspect, thinking, timeout, retries, log, session, avoid=None):
    model = model.strip()
    if "=" in model or not model.startswith("gemini-"):
        raise PaintError(f"그림 모델 이름이 이상합니다: {model!r} → .env 에 GEMINI_IMAGE_MODEL=gemini-nano-banana-2.1 "
                         "처럼 적고, 같은 이름의 Windows 환경변수가 있으면 지우세요 (환경변수가 .env 보다 우선)")
    key = api_key("GEMINI_API_KEY")
    if not key:
        raise PaintError("GEMINI_API_KEY 없음 (.env 확인)")
    refs = refs or []
    body = build_body(req, refs, model, aspect, thinking, avoid)
    http = session or requests
    t0, wait = time.time(), 5.0
    for attempt in range(1, retries + 2):
        a0 = time.time()
        try:
            r = http.post(URL, json=body, timeout=timeout,
                          headers={"x-goog-api-key": key, "Content-Type": "application/json"})
            code, msg = r.status_code, ("" if r.status_code == 200 else _error_message(r))
        except requests.Timeout:
            r, code, msg = None, "timeout", f"{timeout:.0f}초 안에 응답 없음"
        except requests.RequestException as e:
            r, code, msg = None, "conn", f"연결 오류 {type(e).__name__}"
        log(f"  [gemini] 시도 {attempt}: {code} {msg} ({time.time() - a0:.0f}초)")

        if code == 200:
            data = r.json()
            img = find_image(data) or find_image(data, skip_thoughts=False)
            if img is None:
                text = find_text(data)[:200]
                raise PaintError(f"응답에 그림이 없습니다{': ' + text if text else ''}")
            raw = base64.b64decode(img["data"])
            return Image.open(io.BytesIO(raw)).convert("RGB"), {
                "model": model, "seconds": round(time.time() - t0, 1), "attempts": attempt,
                "refs": len(refs), "thinking": thinking or "default"}

        retriable = code in (429, 500, 502, 503, 504, "timeout", "conn")
        if retriable and attempt <= retries:
            log(f"  [gemini] {wait:.0f}초 뒤 다시 시도")
            time.sleep(wait)
            wait *= 2
            continue
        if code == 503:
            raise _Busy(f"구글 서버 혼잡으로 처리 못함 (503). 결제·키 문제 아님 — 잠시 뒤 다시")
        if code == 429:
            raise _Busy(f"요청 한도 초과 (429) — 키의 결제 등급·한도 확인")
        if code in (400, 404):
            raise PaintError(f"요청 형식·모델 오류 ({code}, {model}): {msg}")
        if code in (401, 403):
            raise PaintError(f"키 권한 오류 ({code}): {msg}")
        raise PaintError(f"그림 생성 실패 ({code}, {model}): {msg}")
    raise PaintError("unreachable")
