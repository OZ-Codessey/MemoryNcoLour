"""
어떤 요소 때문에 우리 요청만 거절(503)되는지 찾기 — 같은 시각에 변형들을 번갈아 보낸다.

실행 (MemoryNcoLour-main 에서):
    python scripts/ladder_gemini.py              # 변형마다 2번 (최대 12요청)
    python scripts/ladder_gemini.py --tries 3

변형 (모두 out/plan_cache.json 의 가장 최근 팔레트 계획으로 만든 '실제 글'을 쓴다):
  A 단순       : 글 한 줄 (대조군)
  B 긴글       : 스타일+요청을 '문자열 하나'로만 (다른 옵션 없음)  ← 단순과 다른 건 '글 길이'뿐
  C 긴글+형식  : B + response_format(jpeg·4:5·1K)
  D 시스템분리 : 스타일은 system_instruction, 요청만 input 문자열
  E 조각목록   : B 를 [{"type":"text"}] 목록으로
  F 조각+그림  : E + 레퍼런스 1장
→ A 는 되는데 B 부터 안 되면 '글 길이', B 는 되는데 C 가 안 되면 'response_format' … 처럼 읽는다.
재시도 없이 각 1번. 성공 1장 ≈ $0.03. 결과 그림은 out/ladder/ 에 저장.
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
from prompts import color_prompt_v2 as v2                          # noqa: E402
from prompts.paint_prompt import build_paint_request               # noqa: E402
from services.painter import URL, find_image, load_references      # noqa: E402
from services.claude_client import api_key                         # noqa: E402

OUT = ROOT / "out" / "ladder"
SIMPLE = "An abstract color field painting of a calm sunset by a river. No objects, no text."
FORMAT = {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": "4:5", "image_size": "1K"}


def send(body, key, timeout=120):
    t0 = time.time()
    try:
        r = requests.post(URL, json=body, timeout=timeout, headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    except requests.RequestException as e:
        return "conn", time.time() - t0, None
    if r.status_code != 200:
        return r.status_code, time.time() - t0, None
    data = r.json()
    return 200, time.time() - t0, (find_image(data) or find_image(data, skip_thoughts=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tries", type=int, default=2)
    ap.add_argument("--model")
    a = ap.parse_args()
    key = api_key("GEMINI_API_KEY")
    model = a.model or os.getenv("GEMINI_IMAGE_MODEL") or "gemini-nano-banana-2.1"
    cache = json.loads((ROOT / "out" / "plan_cache.json").read_text(encoding="utf-8"))
    ck = list(cache)[-1]
    ok, errors, plan = v2.validate_and_normalize(cache[ck])
    if not ok:
        sys.exit(f"저장된 계획 형식 오류: {errors}")
    req = build_paint_request(plan, ck)
    long_text = req["system"] + "\n\n" + req["request"]
    refs = load_references(limit=1)
    ref_part = [{"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(refs[0]).decode()}] if refs else []

    variants = [
        ("A 단순", {"model": model, "input": SIMPLE}),
        ("B 긴글", {"model": model, "input": long_text}),
        ("C 긴글+형식", {"model": model, "input": long_text, "response_format": FORMAT}),
        ("D 시스템분리", {"model": model, "system_instruction": req["system"], "input": req["request"]}),
        ("E 조각목록", {"model": model, "input": [{"type": "text", "text": long_text}]}),
        ("F 조각+그림", {"model": model, "input": [{"type": "text", "text": req["reference_intro"]}, *ref_part,
                                                 {"type": "text", "text": long_text}]}),
    ]
    print(f"모델 {model} · 긴 글 {len(long_text)}자 · 변형마다 {a.tries}번씩 번갈아\n")
    OUT.mkdir(parents=True, exist_ok=True)
    score = {name: [] for name, _ in variants}
    for t in range(1, a.tries + 1):
        for name, body in variants:
            code, sec, img = send(body, key)
            good = code == 200 and img is not None
            if good:
                (OUT / f"{name.split()[0]}_{t}.jpg").write_bytes(base64.b64decode(img["data"]))
            score[name].append(good)
            print(f"{'✅' if good else '❌'} {t}회차 {name}: {code} ({sec:.0f}초)", flush=True)
            time.sleep(3)
    print("\n결과")
    for name, s in score.items():
        print(f"  {name:10s} {sum(s)}/{len(s)}")
    (OUT / "ladder.json").write_text(json.dumps(score, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
