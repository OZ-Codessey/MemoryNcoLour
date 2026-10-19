"""
================================================================================
MaC (Memory & Color) — Painting Prompt Builder (v2.0)
================================================================================
color_prompt_v2 의 검증된 팔레트 계획(plan) → 그림 모델(Gemini)에 보낼 지시문.

- 고정 스타일: paint_style_system.txt (사물 없음 · 지정 색만 · 스푸마토 · 붓질 · 레퍼런스는 질감만)
  v2.2: 짧게 줄임(약 1,900자 → 약 950자). painter 가 요청 글 앞에 붙여 보낸다 (system_instruction 안 씀)
- 요청마다 바뀌는 부분: 재료 · 기억 질감(경계) · 붓질 에너지 · 분위기 단어 · 구도 힌트 · 팔레트
- 그림 모델에는 원문 문장도, 영문 색 이름도 넘기지 않는다.
  ("Cherry Blossom Pink" 같은 이름이 벚꽃을 그리게 만들 수 있어서) → HEX + 코드가 계산한 색 묘사 + 면적만.
- 구도 힌트는 문장 키(sentence_key)로 고른다 → 같은 문장은 같은 힌트, 문장이 바뀌면 구도도 바뀐다.
"""

import hashlib
import math
from pathlib import Path

try:
    from .color_association_v2 import MEMORY_QUALITY_RULES
except ImportError:
    from color_association_v2 import MEMORY_QUALITY_RULES

STYLE_FILE = Path(__file__).with_name("paint_style_system.txt")

MEDIUM_CLAUSES = {
    "oil": ("Oil on coarse canvas. Sfumato through layered translucent glazes, scumbling with dry-brush "
            "strokes dragged over the canvas tooth, occasional expressive impasto."),
    "acrylic": ("Acrylic on canvas. Soft blended glazes at the boundaries, visible flat-brush strokes, "
                "slightly matte layered texture."),
    "watercolor": ("Watercolor on cold-press paper. Sfumato-like softness through wet-in-wet bleeding and soft "
                   "blooms, transparent layers, visible granulation and paper texture."),
}
ENERGY_CLAUSES = {
    "calm": "slow, broad, quiet strokes with large resting fields",
    "moderate": "flowing, varied strokes with a gentle rhythm",
    "vivid": "energetic, rapid, overlapping strokes with lively small accents",
}
COMPOSITION_HINTS = [
    "color fields drifting diagonally across the surface",
    "an off-center luminous bloom spreading outward",
    "fragmented fields scattered loosely across the whole canvas",
    "sweeping curved currents of color",
    "dense overlapping translucent veils",
    "a heavy lower mass dissolving into lighter air above",
    "broad horizontal drifts broken by irregular patches",
]
AVOID_INTRO = ("The next image is the PREVIOUS painting for this memory. Paint something clearly different: "
               "a different composition, different placement of the color fields and different stroke directions. "
               "Do not reuse its layout.")
REFERENCE_INTRO = ("The following images are STYLE REFERENCES ONLY. Use them for brushwork, paint texture, "
                   "edge softness and irregular composition. Do NOT copy their colors, palette or layout.")


# ------------------------------------------------------------------------------
# HEX → 영어 색 묘사 (그림 모델이 HEX 를 잘 못 읽을 때를 대비한 보조 설명)
# ------------------------------------------------------------------------------
def _hex_to_lch(hx: str):
    r, g, b = (int(hx[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    X = (lin[0] * 0.4124564 + lin[1] * 0.3575761 + lin[2] * 0.1804375) / 0.95047
    Y = lin[0] * 0.2126729 + lin[1] * 0.7151522 + lin[2] * 0.0721750
    Z = (lin[0] * 0.0193339 + lin[1] * 0.1191920 + lin[2] * 0.9503041) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(X), f(Y), f(Z)
    L, a, bb = 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


# CIELAB 색상각 기준 (sRGB 원색 위치: 빨강≈40, 노랑≈102, 초록≈136, 시안≈196, 파랑≈306, 마젠타≈328)
_HUES = [(20, "rose"), (44, "red"), (52, "red-orange"), (72, "orange"), (92, "amber"), (106, "yellow"), (125, "yellow-green"),
         (165, "green"), (205, "teal"), (245, "cyan-blue"), (312, "blue"), (322, "violet"),
         (348, "magenta-purple"), (360, "rose")]


def describe_hex(hx: str) -> str:
    L, C, h = _hex_to_lch(hx)
    light = ("very dark" if L < 25 else "dark" if L < 45 else "medium" if L < 65
             else "light" if L < 82 else "very light")
    if C < 8:
        tint = "" if C < 3 else (" warm" if 20 <= h < 110 else " cool")
        return f"{light}{tint} neutral grey" if L < 92 else f"{light}{tint} off-white"
    sat = "greyish" if C < 22 else "soft" if C < 45 else "vivid"
    hue = next(name for bound, name in _HUES if h < bound)
    return f"{light} {sat} {hue}"


# ------------------------------------------------------------------------------
# 지시문 조립
# ------------------------------------------------------------------------------
def load_style_system() -> str:
    return STYLE_FILE.read_text(encoding="utf-8").strip()


def pick_composition(key: str, avoid: list[str] | None = None) -> str:
    """문장 키로 구도 힌트를 고른다. avoid 에 든 힌트(직전 그림들에 쓴 것)는 건너뛴다."""
    n = int(hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:8], 16)
    avoid = set(avoid or [])
    for i in range(len(COMPOSITION_HINTS)):
        hint = COMPOSITION_HINTS[(n + i) % len(COMPOSITION_HINTS)]
        if hint not in avoid:
            return hint
    return COMPOSITION_HINTS[n % len(COMPOSITION_HINTS)]       # 전부 써 봤으면 처음부터 다시


def build_paint_request(plan: dict, key: str, aspect: str = "4:5", avoid_compositions: list[str] | None = None) -> dict:
    """plan: validate_and_normalize 가 돌려준 plan. key: 문장 고유 키(sentence_key).
    반환: {system, reference_intro, request, composition}
      - 시스템 지시를 받는 모델: system 은 system_instruction 으로, 나머지는 input 으로
      - 받지 않는 모델: as_single_text(...) 로 한 덩어리로 합쳐 보낸다
    """
    painting = plan.get("painting", {})
    texture = plan.get("texture", "없음")
    edges = MEMORY_QUALITY_RULES.get(texture, MEMORY_QUALITY_RULES["없음"])["edges"]
    composition = pick_composition(key, avoid_compositions)
    mood = ", ".join(painting.get("mood_keywords") or []) or "quiet, personal"

    palette = sorted(plan["palette"], key=lambda c: -c["share"])
    lines = "\n".join(
        f"- {c['hex']} ({describe_hex(c['hex'])}) — about {round(c['share'] * 100)}% of the canvas"
        for c in palette
    )
    request = f"""Paint one painting now. Aspect ratio {aspect}, full bleed.
MEDIUM: {MEDIUM_CLAUSES.get(painting.get('medium'), MEDIUM_CLAUSES['acrylic'])}
EDGES: {edges}
ENERGY: {ENERGY_CLAUSES.get(painting.get('energy'), ENERGY_CLAUSES['moderate'])}
MOOD: {mood}
COMPOSITION: {composition}
PALETTE (use only these colors; percentages are approximate canvas area):
{lines}"""
    return {"system": load_style_system(), "reference_intro": REFERENCE_INTRO, "avoid_intro": AVOID_INTRO,
            "request": request, "composition": composition}


def as_single_text(req: dict, with_references: bool) -> tuple:
    """시스템 지시를 받지 않는 모델용: (레퍼런스 앞 글, 레퍼런스 뒤 글)."""
    before = req["system"] + ("\n\n" + req["reference_intro"] if with_references else "")
    return before, req["request"]
