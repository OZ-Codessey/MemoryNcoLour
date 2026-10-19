"""
같은 시각에 '단순 요청'과 '실제 요청(try_paint 와 똑같은 내용)'을 번갈아 보내 비교한다.
  - 둘이 같이 성공/같이 실패 → 서버 혼잡 (우리 요청 문제 아님)
  - 단순 요청만 계속 성공, 실제 요청만 계속 실패 → 우리 요청(크기·내용)이 원인

실행 (MemoryNcoLour-main 에서):
    python scripts/ab_gemini.py                 # 3번 번갈아 (최대 6요청, 성공 1장 ≈ $0.03)
    python scripts/ab_gemini.py --rounds 2
    python scripts/ab_gemini.py --refs 1        # 실제 요청의 레퍼런스를 1장으로 줄여서

실제 요청은 out/plan_cache.json 에 저장된 가장 최근 팔레트 계획으로 만든다 (Claude 호출 없음).
재시도 없이 각 요청 1번씩. 결과 그림은 out/ab/ 에 저장.
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
from services.painter import URL, build_body, find_image, load_references  # noqa: E402
from services.claude_client import api_key                         # noqa: E402

OUT = ROOT / "out" / "ab"
SIMPLE = "An abstract color field painting of a calm sunset by a river. No objects, no text."


def send(body, key, timeout=120):
    t0 = time.time()
    try:
        r = requests.post(URL, json=body, timeout=timeout, headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    except requests.RequestException as e:
        return "conn", type(e).__name__, time.time() - t0, None
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")[:90]
        except Exception:
            msg = r.text[:90]
        return r.status_code, msg, time.time() - t0, None
    data = r.json()
    return 200, "그림 받음", time.time() - t0, (find_image(data) or find_image(data, skip_thoughts=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--refs", type=int, default=3)
    ap.add_argument("--model")
    a = ap.parse_args()

    key = api_key("GEMINI_API_KEY")
    model = a.model or os.getenv("GEMINI_IMAGE_MODEL") or "gemini-nano-banana-2.1"
    cache_file = ROOT / "out" / "plan_cache.json"
    if not cache_file.exists():
        sys.exit("out/plan_cache.json 이 없습니다 → try_paint.py 를 한 번 실행해 팔레트 계획을 만든 뒤 다시")
    cache = json.loads(cache_file.read_text(encoding="utf-8"))
    ck = list(cache)[-1]
    ok, errors, plan = v2.validate_and_normalize(cache[ck])
    if not ok:
        sys.exit(f"저장된 계획 형식 오류: {errors}")
    req = build_paint_request(plan, ck)
    refs = load_references(limit=a.refs)
    real = build_body(req, refs, model)
    simple = {"model": model, "input": SIMPLE}
    size_kb = len(json.dumps(real)) // 1024
    print(f"모델 {model} · 실제 요청: 레퍼런스 {len(refs)}장, 크기 약 {size_kb}KB · 단순 요청: 글 한 줄\n")

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for rnd in range(1, a.rounds + 1):
        for name, body in (("단순", simple), ("실제", real)):
            code, msg, sec, img = send(body, key)
            good = code == 200 and img is not None
            if good:
                (OUT / f"r{rnd}_{'simple' if name == '단순' else 'real'}.jpg").write_bytes(base64.b64decode(img["data"]))
            print(f"{'✅' if good else '❌'} {rnd}회차 {name}: {code} {msg} ({sec:.0f}초)", flush=True)
            rows.append({"round": rnd, "kind": name, "code": code, "ok": good, "seconds": round(sec, 1)})
            time.sleep(3)

    s_ok = sum(r["ok"] for r in rows if r["kind"] == "단순")
    r_ok = sum(r["ok"] for r in rows if r["kind"] == "실제")
    n = a.rounds
    print(f"\n단순 {s_ok}/{n} 성공 · 실제 {r_ok}/{n} 성공")
    if s_ok == n and r_ok == 0:
        print("→ 단순 요청만 됨: 실제 요청의 크기·내용이 원인일 가능성 큼 (--refs 1 로 다시 시험해 보기)")
    elif s_ok == 0 and r_ok == 0:
        print("→ 둘 다 안 됨: 서버 혼잡 (우리 요청 문제 아님)")
    elif r_ok > 0:
        print("→ 실제 요청도 성공함: 우리 요청 형식은 정상. 실패는 그때그때 혼잡 때문")
    else:
        print("→ 섞여 있음: 혼잡이 오락가락하는 중")
    (OUT / "ab.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
