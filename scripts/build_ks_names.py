"""
KS A 0011 색이름표 만들기 (오프라인, 한 번만) → services/data/ks_names.json
================================================================================
사이트(Vercel)는 이 JSON 과 services/ks_systematic.py(규칙) 만 쓴다 (colour-science 불필요).

담는 것
  1. idioms  : KS 관용색이름(관용·계통+관용)과 화면 HEX·Lab → 런타임에서 ΔE00 ≤ 5 이면 그 이름
  2. allowed : KS 표 7 의 색이름별 허용 수식어 (계통색이름 규칙이 씀)
  3. grid    : sRGB 17×17×17 격자(간격 16)마다 먼셀 값 (V, C·cos h, C·sin h) → 런타임에서 보간 후 규칙 적용
  4. rule_accuracy : 규칙을 KS 관용색 199개의 공식 계통색이름과 비교한 정확도

실행 (pip install colour-science 필요, 2코어 약 10분):
    python scripts/build_ks_names.py --ks backend_ref/data/kscorpus
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import sys
import warnings
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.ks_systematic import (load_table7, normalize, parse_munsell, systematic_name,  # noqa: E402
                                    table_to_json)

OUT = ROOT / "services" / "data" / "ks_names.json"
LEVELS = list(range(0, 256, 16)) + [255]


def _munsell(rgb):
    """sRGB(0~255) → (V, X, Y)  X,Y = C·cos/sin(h·2π/100). 실패하면 Lab 근사."""
    import colour
    warnings.filterwarnings("ignore")
    XY_C = colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["C"]
    XY_D65 = colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["D65"]
    XYZ = colour.sRGB_to_XYZ(np.array(rgb) / 255)
    XYZc = colour.adaptation.chromatic_adaptation_VonKries(XYZ, colour.xyY_to_XYZ([*XY_D65, 1]),
                                                           colour.xyY_to_XYZ([*XY_C, 1]), transform="Bradford")
    try:
        m = colour.notation.munsell.xyY_to_munsell_colour(colour.XYZ_to_xyY(XYZc))
        h, V, C = parse_munsell(m)
        ok = True
    except Exception:
        L = colour.XYZ_to_Lab(XYZ)
        V, C = float(L[0]) / 10, float(np.hypot(L[1], L[2])) / 5.5
        h = _approx_hue(float(np.degrees(np.arctan2(L[2], L[1])) % 360))
        ok = False
    if h is None:
        return V, 0.0, 0.0, ok
    t = h / 100 * 2 * math.pi
    return V, C * math.cos(t), C * math.sin(t), ok


_HUE_MAP = [(0, 95), (25, 2.5), (40, 7.5), (55, 12.5), (70, 17.5), (85, 22.5), (100, 27.5), (115, 32.5),
            (135, 37.5), (160, 45), (190, 55), (220, 65), (260, 72.5), (290, 80), (320, 87.5), (345, 92.5), (360, 95)]


def _approx_hue(deg: float) -> float:
    for (a0, h0), (a1, h1) in zip(_HUE_MAP, _HUE_MAP[1:]):
        if a0 <= deg <= a1:
            if h1 < h0:
                h1 += 100
            return (h0 + (h1 - h0) * (deg - a0) / (a1 - a0)) % 100
    return 0.0


def main():
    import colour
    ap = argparse.ArgumentParser()
    ap.add_argument("--ks", default=str(ROOT / "backend_ref" / "data" / "kscorpus"))
    a = ap.parse_args()
    ks_dir = Path(a.ks)
    table = load_table7(ks_dir / "source" / "ks_table7.tsv")
    ks = json.loads((ks_dir / "ks_colors.json").read_text(encoding="utf-8"))

    hit = base_hit = 0
    for c in ks["colors"]:
        h, V, C = parse_munsell(c["munsell"])
        got, want = normalize(systematic_name(h, V, C, table)), normalize(c.get("systematic") or c["name"])
        hit += got == want
        base_hit += got.split()[-1] == want.split()[-1]
    n = len(ks["colors"])
    print(f"규칙 정확도: 정확 {hit}/{n} ({hit / n:.0%}) · 색 이름(수식어 제외) {base_hit / n:.0%}")

    idioms = []
    for c in ks["colors"]:
        if "관용" in c["kind"]:
            rgb = np.array([int(c["hex"][i:i + 2], 16) for i in (1, 3, 5)]) / 255
            lab = colour.XYZ_to_Lab(colour.sRGB_to_XYZ(rgb))
            idioms.append([c["name"], c["hex"], *[round(float(v), 2) for v in lab]])
    print(f"관용색 {len(idioms)}개")

    pts = [(r, g, b) for r in LEVELS for g in LEVELS for b in LEVELS]
    with Pool() as pool:
        res = []
        for i, out in enumerate(pool.imap(_munsell, pts, chunksize=32)):
            res.append(out)
            if i % 250 == 0:
                print(f"  먼셀 변환 {i}/{len(pts)}", end="\r")
    failed = sum(not r[3] for r in res)
    grid = np.array([r[:3] for r in res], np.float32)
    print(f"\n격자 {len(pts)}점 · 변환 실패 {failed}점(Lab 근사)")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "version": "ks-names-1",
        "source": "KS A 0011:2015 표 7(계통색이름)·표 8(관용색이름) — backend_ref/data/kscorpus",
        "rule_accuracy": {"exact": round(hit / n, 3), "base_name": round(base_hit / n, 3), "n": n},
        "idiom_de00": 5.0,
        "idioms": idioms,
        "allowed": table_to_json(table),
        "levels": LEVELS,
        "grid": base64.b64encode(grid.tobytes()).decode(),
    }, ensure_ascii=False), encoding="utf-8")
    print(f"저장: {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
