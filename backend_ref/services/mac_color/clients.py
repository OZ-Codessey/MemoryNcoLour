"""clients.py — 바깥 API 세 개.

  Claude  : 문장 나누기·후보 고르기 (기본) claude_json   — ANTHROPIC_API_KEY (+ ANTHROPIC_BASE_URL, Codyssey)
  Gemini  : 선택 (MAC_*_LLM=gemini 일 때) gemini_json   — GEMINI_API_KEY (팀 services/llm_engine.py 그대로)
  Voyage  : 질의 임베딩                   VoyageEmbedder — VOYAGE_API_KEY

어느 단계에 어느 모델을 쓸지는 환경변수로 바꾼다: MAC_SPLIT_LLM=claude|gemini, MAC_PICK_LLM=claude|gemini (둘 다 기본 claude).
모두 '함수 하나'로 감싸서 pipeline 에 넘긴다 → 시험할 때는 가짜 함수로 바꿔 끼울 수 있다.
키는 환경변수에서만 읽는다 (Vercel 환경변수 / 로컬 .env). 코드·로그에 키를 쓰지 않는다.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]


def _is_placeholder(v: str) -> bool:
    """.env.example 의 '여기에_발급받은_키' 같은 자리 표시 값"""
    return not v or "여기에" in v or v.startswith("<") or v.lower() in ("your_key", "changeme")


def load_env_file(path: Path | None = None) -> None:
    """저장소 맨 위 .env 를 읽어 환경변수에 넣는다 (이미 있는 값은 그대로 — Vercel 환경변수가 우선).
    python-dotenv 가 없어도 동작하도록 직접 읽는다. 키는 출력하지 않는다.
    - 같은 이름이 두 번 있으면 아래 줄이 이긴다 (python-dotenv 와 같음)
    - '여기에_발급받은_키' 같은 자리 표시 값은 없는 것으로 친다"""
    p = Path(path) if path else ROOT / ".env"
    if not p.exists():
        return
    vals = {}
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.split(" #", 1)[0].strip().strip('"').strip("'").strip()
        if k:
            vals[k] = v
    for k, v in vals.items():
        if _is_placeholder(v):
            continue
        if k not in os.environ or _is_placeholder(os.environ[k]):
            os.environ[k] = v


def api_key(name: str) -> str | None:
    """키를 읽고 형식을 확인한다. 키는 영문·숫자·기호만 — 한글·전각 공백 등이 섞이면 HTTP 헤더에 못 실어
    UnicodeEncodeError 가 나므로, 그 전에 어느 키가 문제인지 알려 준다 (키 값은 출력하지 않음)."""
    load_env_file()
    v = (os.getenv(name) or "").strip()
    if _is_placeholder(v):
        return None
    if not v.isascii() or any(c.isspace() for c in v):
        raise RuntimeError(f"{name} 값에 키가 아닌 글자(한글·공백 등)가 섞여 있음 — .env 의 그 줄을 확인")
    return v


_read_key = api_key            # VoyageEmbedder 의 인자 이름(api_key)과 겹치지 않게


def _import_llm_engine():
    """팀 services/llm_engine.py 를 불러온다.
    llm_engine 은 맨 위에서 python-dotenv 를 import 한다 — 그 패키지가 없는 PC 에서는 import 가 통째로 실패하고,
    analyze.py 의 예비 경로 때문에 "No module named 'llm_engine'" 이라는 엉뚱한 메시지만 남았다.
    그래서 dotenv 가 없으면 같은 일을 하는 작은 대체 모듈을 끼워 넣고 불러온다."""
    import sys
    import types
    try:
        import dotenv  # noqa: F401
    except ImportError:
        shim = types.ModuleType("dotenv")
        shim.load_dotenv = lambda *a, **k: (load_env_file(), True)[1]
        sys.modules["dotenv"] = shim
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from google import genai  # noqa: F401  (llm_engine 이 함수 안에서 쓰는 패키지 — 없으면 미리 알려 줌)
    except ImportError as e:
        raise ModuleNotFoundError("google-genai 패키지가 없습니다 → pip install -r requirements.txt", name="google.genai") from e
    from services.llm_engine import analyze_memory_with_llm
    return analyze_memory_with_llm


def gemini_json(prompt: str) -> dict:
    """기존 services/llm_engine.py 를 그대로 쓴다 (모델 순서·재시도·temperature 0·seed 42 동일)."""
    load_env_file()
    return _import_llm_engine()(prompt)


# ---------------------------------------------------------------- Claude (Anthropic Messages API)
load_env_file()
# 팀 키는 Codyssey 공개 API(https://copa.codyssey.kr, sk-cody-… 키)에서 받은 것 — Anthropic Messages 규격 그대로(x-api-key 헤더).
# 주소가 Codyssey 면 그곳 예제 모델(claude-sonnet-4)을, 아니면 Anthropic 최신 Sonnet 을 기본으로 쓴다. MAC_CLAUDE_MODEL 로 바꿈.
CLAUDE_BASE = (os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/").removesuffix("/v1")
CLAUDE_URL = CLAUDE_BASE + "/v1/messages"
CLAUDE_MODEL = os.getenv("MAC_CLAUDE_MODEL") or ("claude-sonnet-4" if "codyssey" in CLAUDE_BASE else "claude-sonnet-5-5")


class ClaudeError(RuntimeError):
    pass


# 요청 옵션 단계: 0 = effort + 구조화 출력 / 1 = 구조화 출력만 / 2 = 둘 다 없이 (지시문으로 JSON 요청)
# 400 으로 거절되면 다음 단계로 내려가고, 통한 단계를 기억해 다음 문장부터는 거기서 시작한다
# (예: Codyssey 의 claude-sonnet-4 처럼 옵션을 받지 않는 모델이면 매 문장 거절 요청을 반복하지 않음)
_VARIANTS = [dict(effort="low", structured=True), dict(effort=None, structured=True), dict(effort=None, structured=False)]
_variant_start = 0


def claude_body(prompt: str, schema: dict | None, *, model: str | None = None, max_tokens: int = 2000,
                effort: str | None = "low", structured: bool = True) -> dict:
    body = {"model": model or CLAUDE_MODEL, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    oc = {}
    if effort:
        oc["effort"] = effort                       # 나누기는 짧은 작업 → 생각을 적게 (응답 시간 단축)
    if schema is not None and structured:
        oc["format"] = {"type": "json_schema", "schema": schema}   # 구조화 출력: 스키마에 맞는 JSON 만 돌려받음
    if oc:
        body["output_config"] = oc
    return body


def _parse_json_text(text: str) -> dict:
    import json
    t = text.strip()
    if t.startswith("```"):                         # 구조화 출력을 못 쓸 때 대비: ```json … ``` 감싸기 제거
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    a, b = t.find("{"), t.rfind("}")
    return json.loads(t[a:b + 1] if a >= 0 and b > a else t)


def claude_json(prompt: str, schema: dict | None = None, *, model: str | None = None, timeout: float = 40.0) -> dict:
    """Claude 에게 지시문 하나를 보내고 JSON(dict) 하나를 받는다.
    - schema 가 있으면 구조화 출력(output_config.format)으로 형식을 강제한다.
    - 429·5xx·연결 실패는 한 번만 짧게 다시 시도한다 (서버 요청 안에서 오래 기다리지 않음).
    - 400(요청 형식 거부)이면 effort → 구조화 출력 순서로 빼고 다시 보낸다 (모델마다 받는 옵션이 다를 수 있음)."""
    import requests
    key = api_key("ANTHROPIC_API_KEY")
    if not key:
        raise ClaudeError("ANTHROPIC_API_KEY 없음 (.env 또는 Vercel 환경변수)")
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    global _variant_start
    v, transient_left = _variant_start, 1
    while True:
        body = claude_body(prompt, schema, model=model, **_VARIANTS[v])
        try:
            r = requests.post(CLAUDE_URL, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            r, err = None, type(e).__name__
        if r is not None and r.status_code == 200:
            out = r.json()
            stop = out.get("stop_reason")
            if stop in ("max_tokens", "refusal"):
                raise ClaudeError(f"Claude 응답이 끝까지 오지 않음 (stop_reason={stop})")
            text = "".join(c.get("text", "") for c in out.get("content", []) if c.get("type") == "text")
            _variant_start = v
            return _parse_json_text(text)
        if r is not None and r.status_code == 400 and v + 1 < len(_VARIANTS):
            v += 1
            continue
        transient = r is None or r.status_code in (429, 529) or r.status_code >= 500
        if transient and transient_left > 0:
            transient_left -= 1
            try:
                wait = min(float(r.headers.get("retry-after", 2)), 5.0) if r is not None else 2.0
            except ValueError:
                wait = 2.0
            time.sleep(wait)
            continue
        if r is None:
            raise ClaudeError(f"Claude 연결 실패 ({err})")
        try:                                         # 오류 종류만 남긴다 (키·문장은 남기지 않음)
            kind = r.json().get("error", {}).get("type", "")
        except Exception:
            kind = ""
        raise ClaudeError(f"Claude 호출 실패: HTTP {r.status_code} {kind}".strip())


def make_llm(name: str, schema: dict | None = None):
    """'claude' / 'gemini' → 지시문 하나를 받아 dict 를 돌려주는 함수."""
    name = (name or "").strip().lower()
    if name == "claude":
        fn = lambda prompt: claude_json(prompt, schema)          # noqa: E731
        fn.label = f"claude:{CLAUDE_MODEL}"
        return fn
    if name == "gemini":
        fn = lambda prompt: gemini_json(prompt)                  # noqa: E731
        fn.label = "gemini:llm_engine"
        return fn
    raise ValueError(f"알 수 없는 모델 이름: {name!r} (claude 또는 gemini)")


# ---------------------------------------------------------------- Voyage
VOYAGE_URL = os.getenv("VOYAGE_API_BASE", "https://api.voyageai.com/v1").rstrip("/") + "/embeddings"


class VoyageEmbedder:
    """코퍼스와 똑같은 설정(model·dim·input_type)으로 질의를 임베딩한다. 설정은 word_vectors.meta.json 에서 온다.

    voyageai SDK 대신 HTTP 요청 한 번으로 부른다 (requests 는 원래 requirements.txt 에 있음).
    SDK 는 tokenizers·langchain·huggingface 등 약 90MB 를 함께 설치해 Vercel 함수 크기 한도(250MB)에 가까워진다.
    보내는 내용은 SDK(voyageai 0.3.x Client.embed)와 글자 하나까지 같다 → 코퍼스 벡터와 같은 공간."""

    def __init__(self, settings: dict, api_key: str | None = None, retries: int = 2, timeout: float = 15.0):
        self.settings = settings
        self._given_key = api_key
        self.retries = retries
        self.timeout = timeout

    def body(self, texts: list[str]) -> dict:
        return {"input": list(texts), "model": self.settings["model"], "input_type": self.settings["input_type"],
                "truncation": True, "output_dtype": None, "output_dimension": self.settings["dim"],
                "encoding_format": "base64"}

    def __call__(self, texts: list[str]) -> np.ndarray:
        import base64

        import requests
        key = self._given_key or _read_key("VOYAGE_API_KEY")   # 형식이 틀리면 여기서 원인을 알려 줌
        if not key:
            raise RuntimeError("VOYAGE_API_KEY 없음")
        wait = 1.0
        for attempt in range(self.retries + 1):
            try:
                r = requests.post(VOYAGE_URL, json=self.body(texts), timeout=self.timeout,
                                  headers={"Authorization": f"Bearer {key}"})
            except requests.RequestException:
                r = None
            if r is not None and r.status_code == 200:
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                m = np.stack([np.frombuffer(base64.b64decode(d["embedding"]), dtype=np.float32)
                              if isinstance(d["embedding"], str) else np.asarray(d["embedding"], dtype=np.float32)
                              for d in data])
                return m / np.linalg.norm(m, axis=1, keepdims=True)
            retriable = r is None or r.status_code == 429 or r.status_code >= 500
            if not retriable or attempt == self.retries:
                code = "연결 실패" if r is None else f"HTTP {r.status_code}"
                raise RuntimeError(f"Voyage 임베딩 실패: {code}")     # 응답 본문·키는 남기지 않는다
            time.sleep(wait)                  # 서버 요청 안에서는 오래 기다리지 않는다 → 실패하면 글자 비교 후보로
            wait *= 2
        raise RuntimeError("unreachable")
