"""
HEX → KS A 0011 색이름 (한국어)
================================================================================
  1. KS 관용색(135개) 중 가장 가까운 색과 ΔE00 ≤ 5 → 그 관용색이름   (예: 살구색, 진주색)
  2. 아니면 → KS 계통색이름 (수식어 + 색이름, 예: 흐린 노란 연두, 회청록, 어두운 남색)
     먼셀 값은 services/data/ks_names.json 의 격자를 보간해서 얻고, 이름은 services/ks_systematic.py 규칙으로.
같은 HEX 는 항상 같은 이름. numpy 만 쓴다 (colour-science 없음 → Vercel 함수 가벼움).
표를 다시 만들 때: python scripts/build_ks_names.py
"""
from __future__ import annotations

import base64
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

try:
    from services.ks_systematic import systematic_name, table_from_json
except ImportError:                     # api/ 에서 services 를 경로로 넣은 경우
    from ks_systematic import systematic_name, table_from_json

DATA = Path(__file__).resolve().parent / "data" / "ks_names.json"


def _rgb_to_lab(rgb) -> np.ndarray:
    c = np.asarray(rgb, float) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def delta_e00(lab1, lab2) -> np.ndarray:
    """CIEDE2000. lab1: (3,), lab2: (N,3)."""
    L1, a1, b1 = np.asarray(lab1, float)
    L2, a2, b2 = np.asarray(lab2, float).T
    C1, C2 = math.hypot(a1, b1), np.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - np.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1, h2 = np.degrees(np.arctan2(b1, a1p)) % 360, np.degrees(np.arctan2(b2, a2p)) % 360
    dL, dC = L2 - L1, C2p - C1p
    dh = h2 - h1
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(C1p * C2p == 0, 0, dh)
    dH = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh / 2))
    Lb, Cbp, hs = (L1 + L2) / 2, (C1p + C2p) / 2, h1 + h2
    hb = np.where(C1p * C2p == 0, hs, np.where(np.abs(h1 - h2) <= 180, hs / 2,
                                               np.where(hs < 360, (hs + 360) / 2, (hs - 360) / 2)))
    T = (1 - 0.17 * np.cos(np.radians(hb - 30)) + 0.24 * np.cos(np.radians(2 * hb))
         + 0.32 * np.cos(np.radians(3 * hb + 6)) - 0.2 * np.cos(np.radians(4 * hb - 63)))
    SL = 1 + 0.015 * (Lb - 50) ** 2 / np.sqrt(20 + (Lb - 50) ** 2)
    SC, SH = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    RT = -2 * np.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) * np.sin(np.radians(60 * np.exp(-((hb - 275) / 25) ** 2)))
    return np.sqrt((dL / SL) ** 2 + (dC / SC) ** 2 + (dH / SH) ** 2 + RT * (dC / SC) * (dH / SH))


@lru_cache(maxsize=1)
def _data():
    d = json.loads(DATA.read_text(encoding="utf-8"))
    levels = np.array(d["levels"], float)
    G = len(levels)
    grid = np.frombuffer(base64.b64decode(d["grid"]), np.float32).reshape(G, G, G, 3)
    ilab = np.array([i[2:5] for i in d["idioms"]], float)
    return d, levels, grid, ilab, table_from_json(d["allowed"])


def _hex_rgb(hx: str) -> np.ndarray:
    hx = hx.strip().lstrip("#")
    return np.array([int(hx[i:i + 2], 16) for i in (0, 2, 4)], float)


def munsell_of(hx: str):
    """HEX → (h 0~100 | None, V, C) — 격자 삼선형 보간."""
    _, levels, grid, _, _ = _data()
    rgb = _hex_rgb(hx)
    idx, w = [], []
    for v in rgb:
        i = min(int(np.searchsorted(levels, v, side="right")) - 1, len(levels) - 2)
        t = (v - levels[i]) / (levels[i + 1] - levels[i])
        idx.append(i)
        w.append(t)
    (i, j, k), (u, s, t) = idx, w
    out = np.zeros(3)
    for di, wi in ((0, 1 - u), (1, u)):
        for dj, wj in ((0, 1 - s), (1, s)):
            for dk, wk in ((0, 1 - t), (1, t)):
                out += wi * wj * wk * grid[i + di, j + dj, k + dk]
    V, X, Y = (float(x) for x in out)
    C = math.hypot(X, Y)
    h = (math.degrees(math.atan2(Y, X)) / 360 * 100) % 100 if C >= 0.3 else None
    return h, V, C


def ks_color_name(hx: str) -> dict:
    """{'name': 한국어 KS 색이름, 'kind': '관용'|'계통', 'de00': 관용색까지 거리, 'idiom': 가까운 관용색이름, 'idiom_hex'}"""
    d, _, _, ilab, table = _data()
    lab = _rgb_to_lab(_hex_rgb(hx))
    de = delta_e00(lab, ilab)
    i = int(de.argmin())
    near = d["idioms"][i]
    if de[i] <= d["idiom_de00"]:
        return {"name": near[0], "kind": "관용", "de00": round(float(de[i]), 1), "idiom": near[0], "idiom_hex": near[1]}
    h, V, C = munsell_of(hx)
    return {"name": systematic_name(h, V, C, table), "kind": "계통", "de00": round(float(de[i]), 1),
            "idiom": near[0], "idiom_hex": near[1]}


def has_batchim(word: str) -> bool:
    """마지막 글자 받침 여부 (조사 고르기: 과/와, 이/가)."""
    ch = word.strip()[-1:] or "가"
    code = ord(ch) - 0xAC00
    return 0 <= code <= 11171 and code % 28 != 0


def apply_ks_names(palette: list[dict]) -> list[dict]:
    """칩 목록의 color_name 을 KS 한국어 이름으로. 원래(영문) 이름은 color_name_en 에 남긴다.
    저장된 예전 결과(영문 이름)도 같은 HEX → 같은 KS 이름으로 다시 붙는다."""
    out = []
    for chip in palette or []:
        chip = dict(chip)
        try:
            ks = ks_color_name(chip["hex"])
        except Exception as e:                       # 이름표 문제로 결과 전체가 실패하지 않게
            print(f"[KS name warning] {chip.get('hex')}: {e}")
            out.append(chip)
            continue
        if chip.get("color_name") and chip.get("color_name") != ks["name"] and "color_name_en" not in chip:
            chip["color_name_en"] = chip["color_name"]
        chip["color_name"] = ks["name"]
        chip["ks_kind"] = ks["kind"]
        out.append(chip)
    return out
