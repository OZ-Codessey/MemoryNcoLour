"""그림 → 칩 시험 (오프라인, API 없음). 실행: python tests/test_measure.py"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.measure import extract_chips  # noqa: E402


def _img(bands):
    """[(hex, 비율)] → 가로 띠 그림 (붓질 대신 약간의 잡음)."""
    rows, rng = [], np.random.default_rng(0)
    for hx, frac in bands:
        rgb = [int(hx[i:i + 2], 16) for i in (1, 3, 5)]
        rows.append(np.tile(rgb, (max(1, int(400 * frac)), 320, 1)))
    a = np.concatenate(rows).astype(float) + rng.normal(0, 2, (sum(len(r) for r in rows), 320, 3))
    return Image.fromarray(np.clip(a, 0, 255).astype("uint8"))


PLAN = [{"hex": "#3A414C", "color_name": "Ashen Indigo", "reason": "a", "role": "dominant", "share": .4},
        {"hex": "#A3AAAF", "color_name": "Muted Steel Fog", "reason": "b", "role": "supporting", "share": .2},
        {"hex": "#7B8086", "color_name": "Wet Pavement Grey", "reason": "c", "role": "supporting", "share": .3},
        {"hex": "#C8A27A", "color_name": "Faint Amber Light", "reason": "d", "role": "accent", "share": .1}]


def test_near_duplicate_grey_gives_way_to_accent():
    # 회색 두 톤(ΔE≈10) + 작은 호박색 → 호박색이 칩에 들어와야 한다
    img = _img([("#3A414C", .40), ("#7B8086", .20), ("#62686F", .17), ("#A3AAAF", .19), ("#B58A55", .04)])
    chips = extract_chips(img, PLAN, seed=1)["chips"]
    names = [c["color_name"] for c in chips]
    assert any(c["name_source"] != "described" and "Amber" in c["color_name"] for c in chips), names
    assert abs(sum(c["ratio"] for c in chips) - 1) < 1e-6


def test_plan_name_used_once():
    img = _img([("#3A414C", .45), ("#7B8086", .25), ("#62686F", .30)])     # 다른 색이 없으면 회색 두 톤 모두 칩
    chips = extract_chips(img, PLAN, n=3, seed=1)["chips"]
    names = [c["color_name"] for c in chips]
    assert len(set(names)) == len(names), names
    assert any(c["name_source"] == "tone" for c in chips), [c["name_source"] for c in chips]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
