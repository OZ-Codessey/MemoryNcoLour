"""analyze_vectors.py — API 없이 코퍼스 벡터만으로 매칭 기준값(τ)의 출발점을 잡는다.

방법 (한 개 빼고 찾기)
  같은 단어 줄에 표기가 둘 이상인 경우, 표기 하나를 '코퍼스에 없는 새 표현'이라고 치고
  자기 자신을 뺀 같은 축 표기들 중 가장 가까운 것을 찾는다.
    · 단어 정답: 가장 가까운 표기가 같은 단어 줄이면 정답 (예: '저녁노을' → '노을' 줄)
    · 색 정답: 다른 단어여도 기본 색(첫 번째 색)이 같으면 색으로는 정답
  τ 마다: 받아들이는 비율(점수 ≥ τ) 과 받아들인 것 중 정답 비율을 본다.
한계: 별칭은 원래 단어와 글자가 겹치는 경우가 많아 실제 사용자 표현보다 쉬운 문제다. 실제 기준값은 런타임 평가로 확정한다.

실행  python scripts/analyze_vectors.py [벡터 폴더]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
vdir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "kscorpus"
meta = json.loads((vdir / "word_vectors.meta.json").read_text(encoding="utf-8"))
vec = np.load(vdir / "word_vectors.npz")["vectors"].astype(np.float32)
words = {w["id"]: w for w in json.loads((ROOT / "data" / "kscorpus" / "words.json").read_text(encoding="utf-8"))["words"]}
rows = meta["rows"]
first = {wid: (w["colors"][0]["name"] if w["colors"] else None) for wid, w in words.items()}

by_axis = defaultdict(list)
for i, r in enumerate(rows):
    by_axis[r["axis"]].append(i)
count = defaultdict(int)
for r in rows:
    count[r["word_id"]] += 1

res = []  # (axis, sim, word_ok, color_ok, q, hit)
for axis, idx in by_axis.items():
    if axis == "none":
        continue
    sub = vec[idx]
    sims = sub @ sub.T
    np.fill_diagonal(sims, -1)
    for a, i in enumerate(idx):
        if count[rows[i]["word_id"]] < 2:
            continue
        b = int(np.argmax(sims[a]))
        j = idx[b]
        wq, wh = rows[i]["word_id"], rows[j]["word_id"]
        res.append((axis, float(sims[a, b]), wq == wh, first[wq] == first[wh], rows[i]["text"], rows[j]["text"]))

n = len(res)
print(f"평가한 표기 {n}개 (같은 단어 줄에 표기가 둘 이상인 것)")
print(f"{'τ':>5} {'받아들임':>8} {'단어 정답률':>10} {'색 정답률':>9}")
for tau in [0.0, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9]:
    acc = [r for r in res if r[1] >= tau]
    if not acc:
        continue
    print(f"{tau:>5.2f} {len(acc) / n:>8.0%} {sum(r[2] for r in acc) / len(acc):>10.0%} {sum(r[3] for r in acc) / len(acc):>9.0%}")

print("\n축별 (τ 없이 1등만)")
for axis in by_axis:
    rr = [r for r in res if r[0] == axis]
    if rr:
        print(f"  {axis:13s} {len(rr):4d}개  단어 {sum(r[2] for r in rr) / len(rr):.0%}  색 {sum(r[3] for r in rr) / len(rr):.0%}")

print("\n점수가 높은데 색이 틀린 예 (τ 를 높여도 남는 위험)")
bad = sorted([r for r in res if not r[3]], key=lambda r: -r[1])[:15]
for r in bad:
    print(f"  {r[1]:.2f} [{r[0]}] {r[4]} → {r[5]}")

print("\n점수가 낮은데 맞힌 예 (τ 를 높이면 놓치는 것)")
good = sorted([r for r in res if r[2]], key=lambda r: r[1])[:10]
for r in good:
    print(f"  {r[1]:.2f} [{r[0]}] {r[4]} → {r[5]}")

# 서로 다른 단어 사이 점수 분포 (아무 관계 없는 짝이 얼마나 높게 나오는지)
rng = np.random.default_rng(0)
neg = []
for axis, idx in by_axis.items():
    if axis == "none" or len(idx) < 2:
        continue
    a = rng.choice(idx, 2000)
    b = rng.choice(idx, 2000)
    keep = [(x, y) for x, y in zip(a, b) if rows[x]["word_id"] != rows[y]["word_id"]]
    neg += [float(vec[x] @ vec[y]) for x, y in keep]
neg = np.array(neg)
print(f"\n같은 축 아무 두 단어 점수: 평균 {neg.mean():.2f}, 상위 5% {np.quantile(neg, .95):.2f}, 상위 1% {np.quantile(neg, .99):.2f}, 최대 {neg.max():.2f}")
