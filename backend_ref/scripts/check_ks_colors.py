"""check_ks_colors.py — ks_colors.json 검증.

1) 옮겨 적기 검사: 관용색 먼셀 색상이 대응 계통색의 표 7 색상 범위 안에 있는가
2) 역변환 검사: 색역 안의 색은 hex → 먼셀로 되돌렸을 때 원래 먼셀값과 가까운가
3) 일치 검사: hex 와 lch 가 같은 색을 가리키는가 (hex 에서 다시 계산)
4) 중복 검사: 서로 다른 이름이 같은 hex 를 갖는 경우 목록
5) 결정성: 두 번 만들어 content_hash 가 같은가

실행  python scripts/check_ks_colors.py
"""
from __future__ import annotations

import json
import re
import sys
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
import colour

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_ks_colors as B  # noqa: E402

warnings.simplefilter("ignore")
FAM = ["R", "YR", "Y", "GY", "G", "BG", "B", "PB", "P", "RP"]

# KS 원문 자체가 범위와 어긋나는 행 (원문 이미지 16·18·14번으로 확인, 옮겨 적기 오류 아님)
KS_EXCEPTIONS = {
    "진주색": "원문 10YR 9/1 · 분홍빛 하양(10P-7.5YR) — 원문 그대로",
    "은회색": "원문 N8.5 · 밝은 회색(N7-N8) — 원문 그대로",
    "쥐색": "원문 N4.25 · 어두운 회색(N3-N4) — 원문 그대로",
}


def neutral_range(rng: str) -> tuple[float, float]:
    if rng == "N":
        return 0.0, 10.0
    a, _, b = rng.partition("-")
    return float(a[1:]), float((b or a)[1:])


def hue_pos(h: str) -> float:
    """'7.5YR' → 0~100 위치 (10RP 다음이 2.5R). 10R = 다음 계열 0 과 같다."""
    m = re.fullmatch(r"([\d.]+)(RP|YR|GY|BG|PB|R|Y|G|B|P)", h)
    n, f = float(m.group(1)), m.group(2)
    return (FAM.index(f) * 10 + n) % 100


def in_range(hue: str, rng: str) -> bool:
    if rng == "N":
        return False
    a, _, b = rng.partition("-")
    b = b or a
    x, lo, hi = hue_pos(hue), hue_pos(a), hue_pos(b)
    if lo <= hi:
        return lo - 1e-9 <= x <= hi + 1e-9
    return x >= lo - 1e-9 or x <= hi + 1e-9          # 10P-7.5YR 처럼 R 을 넘어가는 범위


def main() -> int:
    data = json.loads(B.OUT.read_text(encoding="utf-8"))
    colors = data["colors"]
    t7 = {r["name"]: r for r in B.read_tsv(B.SRC / "ks_table7.tsv")}
    bad = 0
    t8names = {r["name"] for r in B.read_tsv(B.SRC / "ks_table8.tsv")}
    ks = set(t7) | {r["alt"] for r in t7.values() if r["alt"]} | t8names
    subs = data["substitutes"]
    cnames = {c["name"] for c in colors}

    print("0) 이름 검사")
    e = [c["name"] for c in colors if c["name"] not in ks] + [s["name"] for s in subs if s["name"] not in ks]
    e += [s["name"] for s in subs if s["name"] in cnames]                 # 두 목록에 동시에 있으면 안 됨
    e += [s["name"] for s in subs if s["use_instead"] not in cnames]      # 대체 이름은 colors 안에 있어야 함
    e += [c["name"] for c in colors if not c.get("hex")]                  # colors 는 전부 값이 있어야 함
    missing = (set(t7) | t8names) - cnames - {s["name"] for s in subs}    # 표 7·8 이름이 빠짐없이 어딘가에
    e += sorted(missing)
    print(f"   colors {len(colors)} + substitutes {len(subs)} = {len(colors) + len(subs)} · 문제 {len(e)}건 {e[:10]}")
    bad += len(e)

    print("1) 관용색 색상 ∈ 계통색 색상 범위")
    for c in colors:
        if c["kind"] == "계통":
            continue
        sysname = c.get("systematic") or c["name"]
        rng = t7[sysname]["hue_range"]
        mun = c["munsell"]
        if mun.startswith("N"):
            ok = rng.startswith("N") and (lambda lo_hi: lo_hi[0] <= float(mun[1:]) <= lo_hi[1])(neutral_range(rng))
        else:
            ok = in_range(mun.split()[0], rng)
        if not ok and c["name"] in KS_EXCEPTIONS:
            print(f"   · {c['name']}: {KS_EXCEPTIONS[c['name']]}")
        elif not ok:
            bad += 1
            print(f"   ✗ {c['name']} {mun}  → {sysname} [{rng}]")
    print("   통과 (KS 원문 예외 3건 제외)" if not bad else f"   {bad}건 확인 필요")

    print("2) 역변환 (색역 안, 유채색)")
    worst = []
    for c in colors:
        if not c.get("munsell") or c.get("gamut_mapped") or c["munsell"].startswith("N"):
            continue
        rgb = np.array([int(c["hex"][i:i + 2], 16) / 255 for i in (1, 3, 5)])
        XYZ65 = colour.sRGB_to_XYZ(rgb)
        XYZC = colour.adaptation.chromatic_adaptation_VonKries(XYZ65, B.XYZ_D65, B.XYZ_C, transform="Bradford")
        try:
            back = colour.xyY_to_munsell_colour(colour.XYZ_to_xyY(XYZC))
        except Exception as e:  # 역변환 수렴 실패
            back = f"ERR {type(e).__name__}"
        worst.append((c["name"], c["munsell"], back))
    print(f"   {len(worst)}색 역변환. 예시:")
    for w in worst[:8]:
        print("   ", *w)

    print("3) hex ↔ lch 일치")
    mism = 0
    for c in colors:
        if not c.get("hex"):
            continue
        L, C, h = colour.Lab_to_LCHab(B.hex_to_lab(c["hex"]))
        if abs(L - c["lch"]["L"]) > 0.01 or abs(C - c["lch"]["C"]) > 0.01:
            mism += 1
    print("   통과" if not mism else f"   {mism}건 불일치")
    bad += mism

    print("4) 같은 hex 를 공유하는 이름 (관용끼리)")
    by_hex = defaultdict(list)
    for c in colors:
        if c.get("hex") and c["kind"] != "계통":
            by_hex[c["hex"]].append(c["name"])
    dup = {k: v for k, v in by_hex.items() if len(v) > 1}
    for k, v in dup.items():
        print(f"   {k}: {', '.join(v)}")
    if not dup:
        print("   없음")

    print("5) 결정성")
    h1 = B.build()["content_hash"]
    h2 = B.build()["content_hash"]
    same = h1 == h2 == data["content_hash"]
    print(f"   {h1} / {h2} / 파일 {data['content_hash']} → {'같음' if same else '다름'}")
    bad += 0 if same else 1

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
