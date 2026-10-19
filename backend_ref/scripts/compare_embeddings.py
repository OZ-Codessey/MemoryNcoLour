"""compare_embeddings.py — 임베딩 설정 여러 개를 같은 시험으로 비교한다 (각자 PC 에서, 자기 키로).

시험 (한 개 빼고 찾기)
  같은 단어 줄에 표기가 둘 이상이면, 표기 하나를 '코퍼스에 없는 새 표현'으로 치고
  자기를 뺀 같은 축 표기들 중에서 가장 가까운 단어를 찾는다.
  · 1등 단어 / 1등 색: 가장 가까운 단어가 같은 단어 줄인지 / 기본 색이 같은지
  · 상위 10·20 안 색: Gemini 에게 넘길 후보 목록 안에 맞는 색이 있는지 (RAG 대체 경로의 성공률)
  · τ=0.85 이상 정밀도/받아들임: 높은 기준값으로 바로 받아들일 때 얼마나 맞고 얼마나 받아들이는지

실행
  python scripts/compare_embeddings.py --dry-run   # 설정별 예상 토큰만
  python scripts/compare_embeddings.py             # 설정마다 4,529개씩 임베딩 → 표 출력
결과    화면 표 + data/kscorpus/compare/report.txt  (이 파일 내용만 보내 주면 됨)
        설정별 벡터는 data/kscorpus/compare/<이름>.npz 에 남아 다시 돌릴 때 재사용
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
KS = ROOT / "data" / "kscorpus"
OUT = KS / "compare"

# 이름: (모델, 차원, input_type, 글자 형식)  — {text}=표기, {axis}=축 한글 이름
CONFIGS = {
    "A_v4_512_word":        ("voyage-4", 512, None, "{text}"),            # 지금 쓰는 설정
    "B_v4_1024_word":       ("voyage-4", 1024, None, "{text}"),
    "C_v4_512_axis":        ("voyage-4", 512, None, "{axis}: {text}"),
    "D_v4_512_query":       ("voyage-4", 512, "query", "{text}"),
    "E_v4large_1024_word":  ("voyage-4-large", 1024, None, "{text}"),
    "F_v4large_1024_axis":  ("voyage-4-large", 1024, None, "{axis}: {text}"),
}
AXIS_LABEL = {"emotion": "감정", "memory_color": "사물", "time": "시간", "space": "장소",
              "temperature": "온도", "light": "빛", "sensory": "감각", "none": "기타"}


def load_key() -> str | None:
    if os.environ.get("VOYAGE_API_KEY"):
        return os.environ["VOYAGE_API_KEY"].strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("VOYAGE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def embed(texts, model, dim, itype, key, batch=1000):
    import voyageai
    from voyageai import error as verr
    vo = voyageai.Client(api_key=key, max_retries=0)
    out, tokens = [], 0
    for i in range(0, len(texts), batch):
        part, wait = texts[i:i + batch], 20
        for attempt in range(8):
            try:
                r = vo.embed(part, model=model, input_type=itype, output_dimension=dim)
                out.extend(r.embeddings)
                tokens += r.total_tokens
                break
            except verr.RateLimitError:
                print(f"    사용량 제한 — {wait}초 대기 ({attempt + 1}/8)")
                time.sleep(wait)
                wait = min(wait * 2, 120)
            except verr.AuthenticationError:
                sys.exit("API 키가 올바르지 않다 (.env 확인, 키 값은 출력하지 않음)")
            except (verr.ServiceUnavailableError, verr.ServerError, verr.APIConnectionError) as e:
                print(f"    일시 오류 {type(e).__name__} — {wait}초 대기")
                time.sleep(wait)
                wait = min(wait * 2, 120)
        else:
            sys.exit("계속 실패 — 잠시 뒤 다시 실행 (끝난 설정은 재사용됨)")
        done = min(i + batch, len(texts))
        print(f"    {done}/{len(texts)}")
    m = np.asarray(out, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True), tokens


def evaluate(rows, vec, first):
    cnt = defaultdict(int)
    for r in rows:
        cnt[r["word_id"]] += 1
    by = defaultdict(list)
    for i, r in enumerate(rows):
        if r["axis"] != "none":
            by[r["axis"]].append(i)
    n = top1w = top1c = c10 = c20 = 0
    hi_n = hi_ok = 0
    for idx in by.values():
        sub = vec[idx]
        S = sub @ sub.T
        np.fill_diagonal(S, -1)
        for a, i in enumerate(idx):
            wq = rows[i]["word_id"]
            if cnt[wq] < 2:
                continue
            n += 1
            ranked = []
            for b in np.argsort(-S[a]):
                wid = rows[idx[b]]["word_id"]
                if wid not in ranked:
                    ranked.append(wid)
                if len(ranked) >= 20:
                    break
            top1w += ranked[0] == wq
            top1c += first[ranked[0]] == first[wq]
            c10 += any(first[t] == first[wq] for t in ranked[:10])
            c20 += any(first[t] == first[wq] for t in ranked[:20])
            if S[a].max() >= 0.85:
                hi_n += 1
                hi_ok += first[ranked[0]] == first[wq]
    return {"n": n, "1등 단어": top1w / n, "1등 색": top1c / n, "상위10 색": c10 / n, "상위20 색": c20 / n,
            "τ.85 정밀도": (hi_ok / hi_n) if hi_n else 0, "τ.85 받아들임": hi_n / n}


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", nargs="*", help="이 이름의 설정만 (예: A_v4_512_word C_v4_512_axis)")
    ap.add_argument("--batch", type=int, default=1000, help="한 요청에 보내는 표기 수 (무료 계정은 요청 횟수 제한이 낮아 크게 보내는 편이 빠름, 최대 1000)")
    args = ap.parse_args()

    words = json.loads((KS / "words.json").read_text(encoding="utf-8"))
    rows = [{"text": s, "word_id": w["id"], "axis": w["axis"]} for w in words["words"] for s in [w["word"]] + w["aliases"]]
    first = {w["id"]: (w["colors"][0]["name"] if w["colors"] else None) for w in words["words"]}
    configs = {k: v for k, v in CONFIGS.items() if not args.only or k in args.only}
    OUT.mkdir(parents=True, exist_ok=True)
    chars = sum(len(r["text"]) for r in rows)
    print(f"표기 {len(rows)}개, 설정 {len(configs)}개 → 설정당 대략 {chars * 2:,} 토큰 이하, 합계 {chars * 2 * len(configs):,} 이하 (무료 2억 토큰)")
    if args.dry_run:
        return
    key = load_key()
    lines = [f"words {words['content_hash']} · 표기 {len(rows)}",
             f"{'설정':22s} {'1등 단어':>8} {'1등 색':>7} {'상위10 색':>9} {'상위20 색':>9} {'τ.85 정밀도':>11} {'τ.85 받아들임':>12}"]
    for name, (model, dim, itype, fmt) in configs.items():
        texts = [fmt.format(text=r["text"], axis=AXIS_LABEL[r["axis"]]) for r in rows]
        tag = hashlib.sha256(json.dumps([model, dim, itype, fmt, texts], ensure_ascii=False).encode()).hexdigest()[:12]
        path = OUT / f"{name}.npz"
        if path.exists() and str(np.load(path)["tag"]) == tag:
            vec = np.load(path)["vectors"].astype(np.float32)
            print(f"{name}: 저장된 벡터 재사용")
        else:
            if not key:
                sys.exit("VOYAGE_API_KEY 가 없다 (.env)")
            print(f"{name}: 임베딩 중 ({model}, {dim}, input_type={itype}, 형식 '{fmt}')")
            vec, tok = embed(texts, model, dim, itype, key, batch=max(1, min(args.batch, 1000)))
            print(f"  사용 토큰 {tok:,}")
            np.savez_compressed(path, vectors=vec.astype(np.float16), tag=np.array(tag))
        m = evaluate(rows, vec, first)
        lines.append(f"{name:22s} {m['1등 단어']:>8.0%} {m['1등 색']:>7.0%} {m['상위10 색']:>9.0%} {m['상위20 색']:>9.0%} "
                     f"{m['τ.85 정밀도']:>11.0%} {m['τ.85 받아들임']:>12.0%}")
    report = "\n".join(lines)
    (OUT / "report.txt").write_text(report + "\n", encoding="utf-8")
    print("\n" + report + f"\n\n저장: {(OUT / 'report.txt').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
