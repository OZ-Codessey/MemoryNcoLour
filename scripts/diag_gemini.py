"""
Gemini 그림 요청 진단: 단순 요청에서 시작해 옵션을 하나씩 더하며 어느 옵션에서 실패하는지 찾는다.

실행 (MemoryNcoLour-main 에서):
    python scripts/diag_gemini.py            # 처음부터 차례대로, 첫 실패에서 멈춤
    python scripts/diag_gemini.py --all      # 실패해도 끝까지 (각 옵션을 '단독'으로도 시험)

- SDK 대신 REST 로 직접 보낸다 → SDK 의 '조용한 4번 재시도'가 없어서 결과가 바로 보인다.
- 503 이면 같은 단계를 한 번만 더 시도해 '일시적'인지 '그 옵션 때문'인지 구분한다.
- 성공한 그림은 out/diag/ 에 저장. 성공 1장 ≈ $0.03 (실패는 그림이 없으므로 보통 과금 안 됨).
"""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.claude_client import api_key  # noqa: E402  (.env 읽기)

URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
OUT = ROOT / "out" / "diag"
PROMPT = "An abstract color field painting of a calm sunset by a river. No objects, no text."


def find_image(obj):
    """응답 JSON 어디에 있든 {"type": "image", "data": ...} 를 찾는다 (마지막 것 = 최종 그림)."""
    found = None
    if isinstance(obj, dict):
        if obj.get("type") == "image" and obj.get("data"):
            found = obj
        for v in obj.values():
            found = find_image(v) or found
    elif isinstance(obj, list):
        for v in obj:
            found = find_image(v) or found
    return found


def send(body, key, timeout=120):
    t0 = time.time()
    try:
        r = requests.post(URL, json=body, timeout=timeout,
                          headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    except requests.RequestException as e:
        return None, f"연결 오류 {type(e).__name__}", time.time() - t0, None
    sec = time.time() - t0
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")[:160]
        except Exception:
            msg = r.text[:160]
        return r.status_code, msg, sec, None
    img = find_image(r.json())
    return 200, ("그림 받음" if img else "200 이지만 그림 없음"), sec, img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--model", default=None)
    a = ap.parse_args()

    key = api_key("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY 없음")
    model = a.model or os.getenv("GEMINI_IMAGE_MODEL") or "gemini-nano-banana-2.1"
    ref = sorted((ROOT / "references").glob("ref_*.jpg"))
    ref_b64 = base64.b64encode(ref[0].read_bytes()).decode() if ref else None
    OUT.mkdir(parents=True, exist_ok=True)

    # 옵션 하나씩 (이름, body 를 바꾸는 함수)
    steps = [
        ("기본 (성공했던 단순 요청)", lambda b: b),
        ("+ response_format 이미지·jpeg", lambda b: b.update(response_format={"type": "image", "mime_type": "image/jpeg"})),
        ("+ aspect_ratio 4:5", lambda b: b["response_format"].update(aspect_ratio="4:5") if "response_format" in b
            else b.update(response_format={"type": "image", "mime_type": "image/jpeg", "aspect_ratio": "4:5"})),
        ("+ image_size 1K", lambda b: b["response_format"].update(image_size="1K") if "response_format" in b
            else b.update(response_format={"type": "image", "mime_type": "image/jpeg", "image_size": "1K"})),
        ("+ system_instruction", lambda b: b.update(system_instruction="You are a fine-art painter. Paint pure abstraction.")),
        ("+ thinking minimal", lambda b: b.update(generation_config={"thinking_level": "minimal"})),
        ("+ 입력을 글 조각 목록으로", lambda b: b.update(input=[{"type": "text", "text": PROMPT}])),
        ("+ 레퍼런스 그림 1장", lambda b: b.update(input=[{"type": "text", "text": "Style reference only:"},
                                                     {"type": "image", "mime_type": "image/jpeg", "data": ref_b64},
                                                     {"type": "text", "text": PROMPT}]) if ref_b64 else None),
    ]

    print(f"모델 {model} · 각 단계 1회 (503 이면 1회 더)\n")
    results, body = [], {"model": model, "input": PROMPT}
    for i, (name, change) in enumerate(steps):
        if a.all and i > 0:                      # --all: 각 옵션을 '기본 + 그 옵션 하나'로도 시험
            body = {"model": model, "input": PROMPT}
        change(body)
        code, msg, sec, img = send(body, key)
        if code == 503:
            print(f"  {name}: 503 → 한 번 더")
            code, msg, sec, img = send(body, key)
        ok = code == 200 and img is not None
        if ok:
            raw = base64.b64decode(img["data"])
            (OUT / f"step{i}.{'jpg' if 'jpeg' in img.get('mime_type', '') else 'png'}").write_bytes(raw)
        line = f"{'✅' if ok else '❌'} {i}. {name}: {code} {msg} ({sec:.0f}초)"
        print(line)
        results.append({"step": i, "name": name, "code": code, "msg": msg, "seconds": round(sec, 1), "ok": ok})
        if not ok and not a.all:
            print(f"\n→ 처음 실패한 옵션: '{name}'")
            break
    (OUT / "diag.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n기록: {OUT / 'diag.json'}")


if __name__ == "__main__":
    main()
