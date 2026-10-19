"""
KS A 0011:2015 계통색이름 판정 규칙 (먼셀 H V/C → 이름)
================================================================================
표 7(이름별 색상 범위·허용 수식어)은 services/data/ks_names.json 에 담아 두고, 표에 없는 명도·채도 영역
(원문 부도 1~40)은 KS 관용색 199개에 붙은 공식 계통색이름에 맞춰 보정한 근사 규칙이다.
정확도(관용색 199개 기준): 정확 일치 88% · 색 이름(수식어 제외) 99% — scripts/build_ks_names.py 가 다시 잰다.
(KS 원문 각주: 관용색 대표값은 '색이름 영역의 중심값이 아니며 유일한 대표성을 갖지 않는다')
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

HUE_ORDER = ["R", "YR", "Y", "GY", "G", "BG", "B", "PB", "P", "RP"]


def hue_number(text: str) -> float:
    """'2.5R' → 2.5, '10YR' → 20, '10RP' → 100(=0). 0~100 원형."""
    m = re.fullmatch(r"([\d.]+)([A-Z]+)", text.strip())
    return (HUE_ORDER.index(m.group(2)) * 10 + float(m.group(1))) % 100


def parse_munsell(text: str):
    """'7.5YR 6/6' → (h, V, C), 'N 9.5' / 'N9.5' → (None, V, 0)."""
    t = text.strip()
    if t.startswith("N"):
        return None, float(re.findall(r"[\d.]+", t)[0]), 0.0
    m = re.fullmatch(r"([\d.]+[A-Z]+)\s+([\d.]+)/([\d.]+)", t)
    return hue_number(m.group(1)), float(m.group(2)), float(m.group(3))


def _in(h, lo, hi, pad=1.25):
    """원형 색상 범위 안인가 (양 끝 pad 만큼 너그럽게)."""
    lo, hi = (lo - pad) % 100, (hi + pad) % 100
    return lo <= h <= hi if lo <= hi else (h >= lo or h <= hi)


def load_table7(path: Path) -> dict:
    """빌드용: ks_table7.tsv → {'names': {...}, 'allowed': {(base,combo): set(modifier)}}"""
    rows = [r for r in csv.reader((l for l in open(path, encoding="utf-8") if not l.startswith("#")), delimiter="\t")][1:]
    names, allowed = {}, {}
    for base, combo, mod, name, alt, hr in rows:
        lo = hi = None
        if hr != "N" and not hr.startswith("N"):
            a, *b = hr.split("-")
            lo, hi = hue_number(a), hue_number(b[0] if b else a)
        names[name] = {"base": base, "combo": combo, "modifier": mod, "lo": lo, "hi": hi}
        allowed.setdefault((base, combo), set()).add(mod)
    return {"names": names, "allowed": allowed}


# ---------------------------------------------------------------------------
# 색상마다 '가장 선명한 밝기'(먼셀 명도)가 다르다: 노랑은 밝을 때, 남색·보라는 어두울 때 가장 선명.
# 수식어(밝은/진한 …)는 이 기준 밝기와의 차이(dV)로 판단한다. KS 관용색 대표값으로 맞춘 근사값.
# ---------------------------------------------------------------------------
_PEAK = [(0, 4.0), (5, 4.0), (10, 5.0), (12.5, 5.75), (15, 6.0), (20, 7.5), (22.5, 9.0), (27.5, 9.0),
         (30, 8.5), (35, 7.0), (40, 6.0), (42.5, 4.5), (45, 4.0), (50, 3.5), (60, 3.5), (70, 3.75), (75, 3.0),
         (85, 3.0), (95, 3.5), (100, 4.0)]


def peak_value(h: float) -> float:
    for (h0, v0), (h1, v1) in zip(_PEAK, _PEAK[1:]):
        if h0 <= h <= h1:
            return v0 + (v1 - v0) * (h - h0) / (h1 - h0)
    return 4.0


def hue_family(h: float, V: float, C: float) -> tuple[str, str]:
    """(기본색, 조합색) — 분홍·갈색·남색처럼 명도로 갈리는 이름도 여기서 정한다."""
    dV = V - peak_value(h)
    # 분홍: 빨강~주황·자주 쪽의 밝은 색 (KS 예: 산호색 7.5R 7/8 = 분홍, 새먼핑크 10R 7/8 = 노란 분홍)
    if (h >= 90 or h <= 15) and V >= 6.5 and C <= 12:
        if 90 <= h < 96.25:
            return "분홍", "자줏빛 분홍"
        if 8.75 <= h <= 15:
            return "분홍", "노란 분홍"
        return "분홍", ""
    # 갈색: 빨강~연두 사이에서 기준 밝기보다 어둡고 채도가 아주 높지 않은 색 (황토색 10YR 6/10 = 밝은 황갈색)
    if (8.75 <= h <= 27.5 and dV <= -1.0 and C <= 10.5) or (27.5 < h <= 36.25 and dV <= -1.5 and C <= 6.5):
        if h < 11.25:
            return "갈색", "빨간 갈색"
        if h < 18.75:
            return "갈색", ""
        if h < 26.25:
            return "갈색", "노란 갈색"
        return "갈색", "녹갈색"
    if h >= 98.75 or h < 1.25:
        return "자주", "빨간 자주"
    if h < 8.75:
        return "빨강", ""
    if h < 11.25:
        return "주황", "빨간 주황"
    if h < 16.25:
        return "주황", ""
    if h < 21.25:
        return "주황", "노란 주황"
    if h < 28.75:
        return "노랑", ""
    if h < 33.75:
        return "연두", "노란 연두"
    if h < 38.75:
        return "연두", ""
    if h < 41.25:
        return "연두", "녹연두"
    if h < 56.25:
        return "초록", ""
    if h < 63.75:
        return "청록", ""
    if h < 75:
        return "파랑", ""
    if h < 76.25:
        return ("남색", "") if (h >= 72.5 and V <= 2.5) else ("파랑", "")
    if h < 81.25:
        return ("남색", "") if V <= 4.5 else ("파랑" if h < 77.5 else "보라", "")
    if h < 91.25:
        return "보라", ""
    return "자주", ""


def tint(h: float, V: float) -> str:
    """무채색에 가까운 색의 색 기운 (표 7 하양·회색·검정 조합)."""
    if h >= 92.5 or h <= 7.5:
        return "빨간"
    if h <= 27.5:
        return "노란" if (h >= 17.5 and V >= 6) else "갈"   # 노란 회색(밝음) / 갈회색·갈흑색(어두움)
    if h <= 32.5:
        return "노란"
    if h <= 57.5:
        return "초록빛"
    if h <= 77.5:
        return "파란"
    if h <= 90:
        return "보랏빛"
    return "분홍빛"


TONE_CENTERS = {          # (V, C) 대표점 — 허용 안 된 수식어일 때 가장 가까운 허용 수식어를 고르는 데 씀
    "선명한": (5.0, 14.0), "": (4.5, 10.0), "밝은": (6.5, 10.0), "진한": (3.0, 9.0), "연한": (7.5, 6.5),
    "흐린": (7.5, 4.5), "탁한": (4.0, 5.0), "어두운": (2.5, 4.0),
}


def tone(h: float, V: float, C: float, base: str) -> str:
    dV = V - peak_value(h)
    if base == "분홍":
        if V >= 7.75 and C >= 5.5:
            return "연한"
        return "" if C >= 7 else ("연한" if C >= 5.5 else "흐린")
    if base == "갈색":
        if C >= 9.5:
            return "밝은" if V >= 6 else ""
        if C >= 7:
            return "밝은" if V >= 5 else ("" if V >= 3.5 else "진한")
        if C >= 5.5 and 3.75 <= V < 5 and h > 26.25:      # 녹갈색 (올리브색 10Y 4/6)
            return ""
        if V >= 7:
            return "연한"
        if V >= 6:
            return "흐린"
        return "탁한" if V >= 2.5 else "어두운"
    if C >= 15:
        return "선명한"
    if C >= 10:
        return "밝은" if dV >= 0.75 else ("진한" if dV <= -0.75 else "")
    if C >= 7:
        if dV >= 2 and V >= 7:
            return "연한"
        return "밝은" if dV >= 0.75 else ("진한" if dV <= -0.75 else "")
    # 채도 중간 (4~7)
    if V >= 7.5:
        return "연한" if C >= 6 else "흐린"
    if V >= 6.5:
        return "연한" if C >= 5.5 else "흐린"
    if V >= 2.5:
        return "진한" if (V < 3 and C >= 6) else "탁한"
    return "진한" if C >= 6 else "어두운"


def systematic_name(h, V: float, C: float, table: dict) -> str:
    names, allowed = table["names"], table["allowed"]
    # 1) 무채색 (채도 아주 낮음)
    if h is None or C < 0.8:
        if V >= 9.0:
            return "하양"
        if V >= 6.5:
            return "밝은 회색"
        if V >= 4.5:
            return "회색"
        if V >= 2.5:
            return "어두운 회색"
        return "검정"
    # 2) 색 기운이 있는 무채색 (빨간 회색, 노란 하양 …)
    if C < 2.0:
        t = tint(h, V)
        if V >= 8.75:
            return {"빨간": "분홍빛 하양", "갈": "노란 하양", "노란": "노란 하양", "초록빛": "초록빛 하양",
                    "파란": "파란 하양", "보랏빛": "보랏빛 하양", "분홍빛": "분홍빛 하양"}[t]
        if V <= 2.0:
            return {"빨간": "빨간 검정", "갈": "갈흑색", "노란": "갈흑색", "초록빛": "초록빛 검정",
                    "파란": "파란 검정", "보랏빛": "보랏빛 검정", "분홍빛": "빨간 검정"}[t]
        combo = {"빨간": "빨간 회색", "갈": "갈회색", "노란": "노란 회색", "초록빛": "초록빛 회색",
                 "파란": "파란 회색", "보랏빛": "보랏빛 회색", "분홍빛": "분홍빛 회색"}[t]
        mods = allowed.get(("회색", combo), {""})
        mod = "밝은" if V >= 6.5 and "밝은" in mods else ("어두운" if V < 4 and "어두운" in mods else "")
        return f"{mod} {combo}".strip()
    base, combo = hue_family(h, V, C)
    # 3) 흰·회·검은 계열 (채도 낮음) — 색 계열은 색상으로만 (노란 계열은 어두우면 회갈색)
    if C < 3.5:
        if base == "갈색":
            base = "노랑" if (h >= 17.5 and V >= 5) else "주황"
        grey_combo = {"빨강": "회적색", "주황": "회갈색", "노랑": "회황색", "연두": "회연두", "초록": "회녹색",
                      "청록": "회청록", "파랑": "회청색", "남색": "회남색", "보라": "회보라", "자주": "회자주",
                      "분홍": "회분홍", "갈색": "회갈색"}[base]
        grey_base = "갈색" if grey_combo == "회갈색" else base
        if V >= 8.5:
            return {"노랑": "흰 노랑", "연두": "흰 연두", "초록": "흰 초록", "청록": "흰 청록", "파랑": "흰 파랑",
                    "보라": "흰 보라", "분홍": "흰 분홍", "빨강": "흰 분홍", "자주": "흰 분홍", "주황": "흰 분홍",
                    "남색": "흰 파랑", "갈색": "흰 노랑"}[base]
        if V <= 2.0:
            return {"빨강": "검은 빨강", "자주": "검은 자주", "초록": "검은 초록", "연두": "검은 초록",
                    "청록": "검은 청록", "파랑": "검은 파랑", "남색": "검은 남색", "보라": "검은 보라",
                    "갈색": "검은 갈색", "주황": "검은 갈색", "노랑": "검은 갈색", "분홍": "검은 빨강"}[base]
        mods = allowed.get((grey_base, grey_combo), {""})
        mod = "밝은" if V >= 6.5 and "밝은" in mods else ("어두운" if V < 4.5 and "어두운" in mods else "")
        return f"{mod} {grey_combo}".strip()
    # 4) 보통 유채색: 수식어(톤) — 표 7 에 허용된 것만
    want = tone(h, V, C, base)
    mods = allowed.get((base, combo), {""})
    if want not in mods:
        if want == "진한" and "" in mods:
            want = ""
        else:
            tv, tc = TONE_CENTERS[want]
            want = min(mods, key=lambda m: (TONE_CENTERS[m][0] - tv) ** 2 + ((TONE_CENTERS[m][1] - tc) / 2) ** 2)
    return f"{want} {combo or base}".strip()


# KS 가 허용하는 약칭 (표 7 alt 칸 + 표 8 에 쓰인 형태) — 같은 이름으로 본다
ALIASES = {"황갈색": "노란 갈색", "적갈색": "빨간 갈색", "적자색": "빨간 자주", "진빨강": "진한 빨강",
           "진주황": "진한 주황", "진노랑": "진한 노랑", "연분홍": "연한 분홍", "연보라": "연한 보라",
           "연노랑": "연한 노랑", "연두색": "연두", "녹갈색": "녹갈색"}


def normalize(name: str) -> str:
    name = re.sub(r"\(.*?\)", "", name).strip()
    for short, full in ALIASES.items():
        name = name.replace(short, full)
    return " ".join(name.split())


def table_to_json(table: dict) -> list:
    """allowed 를 JSON 으로: [[base, combo, [modifier…]], …]"""
    return [[b, c, sorted(m)] for (b, c), m in sorted(table["allowed"].items())]


def table_from_json(rows: list) -> dict:
    return {"names": {}, "allowed": {(b, c): set(m) for b, c, m in rows}}
