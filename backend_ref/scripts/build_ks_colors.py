"""build_ks_colors.py — KS A 0011 색이름표(ks_colors.json)를 만든다.

입력  data/kscorpus/source/ks_table8.tsv  (표 8 관용색이름: 이름 · 대응 계통색이름 · 대표 먼셀값)
      data/kscorpus/source/ks_table7.tsv  (표 7 계통색이름: 기본색 · 조합색 · 수식형용사 · 색상 범위)
출력  data/kscorpus/ks_colors.json
        colors      — 실제로 쓰는 색(값이 있는 KS 이름)
        substitutes — KS 이름이지만 값이 없는 계통색 → use_instead 로 바꿔 쓴다
        각주        — 파일을 여는 사람을 위한 설명 (NOTES, 이 파일 아래쪽)

원칙
  · 사람이 hex·LCh 를 손으로 적지 않는다. 모든 색 값은 KS 원문의 먼셀값에서 이 스크립트가 계산한다.
  · 계산 경로: 먼셀 → xyY (C 광원, Munsell Renotation) → XYZ → Bradford 순응 C→D65 → CIELAB(D65) → LCh, sRGB → hex
  · sRGB 로 표현할 수 없는 색은 명도(L)·색상각(h)을 그대로 두고 채도(C)만 줄여 화면 안으로 넣는다 (gamut_mapped=true).
    원래 값은 lch_true 에 남긴다.
  · 계통색이름의 대표값: KS 는 계통색에 점 하나짜리 값을 주지 않는다(표 7 은 색상 범위, 부도는 영역).
    그래서 표 8 에서 그 계통색에 대응하는 관용색의 먼셀값을 쓴다.
      1) 관용색 중 이름이 계통색 이름과 같은 것이 있으면 그것 (예: 계통 '빨강' ← 관용 '빨강' 7.5R 4/14)
      2) 없으면 대응 관용색들 가운데 다른 관용색들과의 ΔE00 합이 가장 작은 것 (메도이드 — 실제 KS 값 하나를 고른다)
      3) 대응 관용색이 없으면 대표값 없음(status=no_rep). 매칭·파생 후보에서 빠진다.
  · 같은 입력이면 항상 같은 출력 (정렬·동점 처리 고정).

실행  python scripts/build_ks_colors.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import warnings
from pathlib import Path

import numpy as np
import colour

warnings.simplefilter("ignore")  # colour 의 정보성 경고(도메인 스케일 안내 등) 숨김

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "kscorpus" / "source"
OUT = ROOT / "data" / "kscorpus" / "ks_colors.json"
VERSION = "ks-1"

OBS = colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]
XY_C, XY_D65 = OBS["C"], OBS["D65"]
XYZ_C, XYZ_D65 = colour.xy_to_XYZ(XY_C), colour.xy_to_XYZ(XY_D65)


# ---------------------------------------------------------------- 읽기
def read_tsv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def split_alt(s: str) -> tuple[str, str]:
    """'진한 빨강(진빨강)' → ('진한 빨강', '진빨강')."""
    m = re.match(r"(.*?)\((.*)\)$", s.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else (s.strip(), "")


# ---------------------------------------------------------------- 색 계산
def munsell_to_lab(munsell: str) -> np.ndarray:
    xyY = colour.munsell_colour_to_xyY(munsell)                    # C 광원 기준
    XYZ = colour.xyY_to_XYZ(xyY)
    XYZ65 = colour.adaptation.chromatic_adaptation_VonKries(XYZ, XYZ_C, XYZ_D65, transform="Bradford")
    return colour.XYZ_to_Lab(XYZ65, illuminant=XY_D65)


def lab_to_linear_rgb(lab: np.ndarray) -> np.ndarray:
    XYZ = colour.Lab_to_XYZ(lab, illuminant=XY_D65)
    return colour.XYZ_to_RGB(XYZ, colour.RGB_COLOURSPACES["sRGB"], illuminant=XY_D65, apply_cctf_encoding=False)


def in_gamut(lab: np.ndarray, eps: float = 1e-6) -> bool:
    rgb = lab_to_linear_rgb(lab)
    return bool(np.all(rgb >= -eps) and np.all(rgb <= 1 + eps))


def gamut_map(lab: np.ndarray) -> tuple[np.ndarray, bool]:
    """L·h 고정, C 만 이분 탐색으로 줄인다 (40회)."""
    if in_gamut(lab):
        return lab, False
    L, C, h = colour.Lab_to_LCHab(lab)
    lo, hi = 0.0, float(C)
    for _ in range(40):
        mid = (lo + hi) / 2
        if in_gamut(colour.LCHab_to_Lab(np.array([L, mid, h]))):
            lo = mid
        else:
            hi = mid
    return colour.LCHab_to_Lab(np.array([L, lo, h])), True


def lab_to_hex(lab: np.ndarray) -> str:
    rgb = colour.XYZ_to_sRGB(colour.Lab_to_XYZ(lab, illuminant=XY_D65), illuminant=XY_D65)
    rgb = np.clip(rgb, 0, 1)
    return "#" + "".join(f"{int(round(c * 255)):02X}" for c in rgb)


def hex_to_lab(hx: str) -> np.ndarray:
    rgb = np.array([int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5)])
    return colour.XYZ_to_Lab(colour.sRGB_to_XYZ(rgb, illuminant=XY_D65), illuminant=XY_D65)


def de00(a: np.ndarray, b: np.ndarray) -> float:
    return float(colour.delta_E(a, b, method="CIE 2000"))


def lch_dict(lab: np.ndarray) -> dict:
    L, C, h = colour.Lab_to_LCHab(lab)
    if C < 0.5:          # 무채색: 색상각은 의미가 없으므로 0 으로 고정
        h = 0.0
    return {"L": round(float(L), 2), "C": round(float(C), 2), "h": round(float(h), 2)}


# ---------------------------------------------------------------- (가) 대체 규칙
# 값이 없는 계통색은 쓰지 않고, 같은 계열에서 값이 있는 KS 이름으로 바꿔 쓴다.
# 수식형용사가 비슷한 톤 순서 (KS 부도의 톤 배치를 따라 가까운 것부터)
TONE_NEAR = {
    "":     ["", "선명한", "진한", "밝은"],
    "선명한": ["선명한", "", "진한", "밝은"],
    "밝은":  ["밝은", "연한", "", "선명한"],
    "연한":  ["연한", "흐린", "밝은", ""],
    "흐린":  ["흐린", "연한", "탁한", ""],
    "탁한":  ["탁한", "흐린", "어두운", ""],
    "어두운": ["어두운", "진한", "탁한", ""],
    "진한":  ["진한", "어두운", "", "선명한"],
}


def implied_tone(combo: str, modifier: str) -> str:
    """조합색이 톤을 담고 있으면(회~, 흰~, 검은~) 기본색으로 내려갈 때 그 톤을 쓴다."""
    if modifier:
        return modifier
    if combo.startswith("회"):
        return "탁한"
    if combo.startswith("흰 "):
        return "흐린"
    if combo.startswith("검은 "):
        return "어두운"
    return ""


def find_substitute(entry: dict, valued: dict[tuple[str, str, str], str]) -> tuple[str | None, str, bool]:
    """반환: (대체 이름, 규칙, 약한 대체 여부).
    약한 대체 = 톤 순서에서 두 칸 이상 밀려났거나, 수식어가 있는 톤이 맨 기본색으로 떨어진 경우
    (단 선명한 → 기본색은 제외: KS 기본색 대표값이 고채도라 실제로 가깝다).
    약한 대체는 이름 매칭에는 써도 되지만, 기억 질감 파생색(연한/흐린/선명한…)으로는 쓰지 않는다."""
    base, combo, mod = entry["base"], entry["combo"], entry["modifier"]
    if combo:                                                    # 1) 같은 조합색 안에서 톤만 바꾸기
        for i, m in enumerate(TONE_NEAR[mod]):
            if (base, combo, m) in valued:
                return valued[(base, combo, m)], "같은 조합색", i >= 2 or (m == "" and mod not in ("", "선명한"))
    want = implied_tone(combo, mod)
    for i, m in enumerate(TONE_NEAR[want]):                      # 2) 기본색으로 내려가서 톤 맞추기
        if (base, "", m) in valued:
            return valued[(base, "", m)], "같은 기본색", i >= 2 or (m == "" and want not in ("", "선명한"))
    return None, "없음", True


def compute(munsell: str) -> dict:
    lab_true = munsell_to_lab(munsell)
    lab_map, mapped = gamut_map(lab_true)
    hx = lab_to_hex(lab_map)
    lab_hex = hex_to_lab(hx)                     # 최종 hex 에서 다시 잰 값 → hex 와 LCh 가 항상 일치
    return {
        "munsell": munsell,
        "hex": hx,
        "lch": lch_dict(lab_hex),
        "lch_munsell": lch_dict(lab_true),
        "gamut_mapped": mapped,
        "de_mapped": round(de00(lab_true, lab_hex), 2),
        "_lab": lab_hex,
    }


# ---------------------------------------------------------------- 만들기
def build() -> dict:
    t7 = read_tsv(SRC / "ks_table7.tsv")
    t8 = read_tsv(SRC / "ks_table8.tsv")

    # 표 7: 계통색 이름 → 정보 (약칭도 같은 행을 가리킨다)
    sys_by_name: dict[str, dict] = {}
    for r in t7:
        sys_by_name[r["name"]] = r
        if r["alt"]:
            sys_by_name[r["alt"]] = r

    # 표 8: 관용색 계산 + 대응 계통색 연결
    idioms = []
    for i, r in enumerate(t8):
        full, alt = split_alt(r["systematic"])
        srow = sys_by_name.get(full) or sys_by_name.get(alt)
        if srow is None:
            raise SystemExit(f"표 8 '{r['name']}'의 대응 계통색 '{r['systematic']}'이 표 7에 없다")
        c = compute(r["munsell"])
        idioms.append({"order": i, "name": r["name"], "kind": "관용",
                       "systematic": srow["name"], "base": srow["base"],
                       "combo": srow["combo"], "modifier": srow["modifier"],
                       "english": r["english"], "ks_page": int(r["page"]), **c})

    # 계통색 대표값: 대응 관용색에서 고른다
    by_sys: dict[str, list[dict]] = {}
    for c in idioms:
        by_sys.setdefault(c["systematic"], []).append(c)

    systematics = []
    for r in t7:
        group = by_sys.get(r["name"], [])
        entry = {"name": r["name"], "kind": "계통", "alt": r["alt"], "base": r["base"],
                 "combo": r["combo"], "modifier": r["modifier"], "hue_range": r["hue_range"]}
        if not group:
            entry.update({"status": "no_rep", "rep_from": None})
            systematics.append(entry)
            continue
        same = [c for c in group if c["name"] in (r["name"], r["alt"])]
        if same:
            pick, how = same[0], "same_name"
        elif len(group) == 1:
            pick, how = group[0], "only"
        else:
            def cost(c):
                return sum(de00(c["_lab"], o["_lab"]) for o in group if o is not c)
            pick = min(group, key=lambda c: (round(cost(c), 6), c["order"]))
            how = "medoid"
        entry.update({"status": "ok", "rep_from": pick["name"], "rep_rule": how,
                      "rep_candidates": [c["name"] for c in group],
                      **{k: pick[k] for k in ("munsell", "hex", "lch", "lch_munsell", "gamut_mapped", "de_mapped")}})
        systematics.append(entry)

    # (가) 값이 없는 계통색 → 값이 있는 KS 이름으로 대체 지정
    valued = {(s["base"], s["combo"], s["modifier"]): s["name"] for s in systematics if s["status"] == "ok"}
    for s in systematics:
        if s["status"] == "no_rep":
            use, how, weak = find_substitute(s, valued)
            s["use_instead"], s["substitute_rule"], s["substitute_weak"] = use, how, weak

    # 이름이 겹치는 경우(관용 '빨강' = 계통 '빨강'): 하나의 항목으로 합친다
    merged: dict[str, dict] = {}
    for s in systematics:
        merged[s["name"]] = s
    for c in idioms:
        c = {k: v for k, v in c.items() if k not in ("_lab", "order")}
        if c["name"] in merged:
            merged[c["name"]]["kind"] = "계통+관용"
            merged[c["name"]]["english"] = c["english"]
            merged[c["name"]]["ks_page"] = c["ks_page"]
        else:
            merged[c["name"]] = c

    # colors = 실제로 쓰는 색(값 있음) / substitutes = 값 없는 KS 이름 → 대신 쓸 이름
    colors, substitutes = [], []
    for x in merged.values():
        if x.get("status") == "no_rep":
            use = merged[x["use_instead"]]
            substitutes.append({
                "name": x["name"], "alt": x["alt"], "base": x["base"], "combo": x["combo"],
                "modifier": x["modifier"], "hue_range": x["hue_range"],
                "use_instead": x["use_instead"], "use_instead_hex": use["hex"],
                "substitute_rule": x["substitute_rule"], "substitute_weak": x["substitute_weak"],
            })
        else:
            x = {k: v for k, v in x.items() if k != "status"}
            x["value_from"] = "KS 직접" if x["kind"] != "계통" else "대응 관용색"
            colors.append(x)

    colors.sort(key=lambda x: (x["kind"] != "관용", x["name"]))
    substitutes.sort(key=lambda x: x["name"])
    blob = json.dumps([colors, substitutes], ensure_ascii=False, sort_keys=True).encode()
    return {
        "version": VERSION,
        "content_hash": hashlib.sha256(blob).hexdigest()[:12],
        "source": "KS A 0011:2015 표 7·표 8 (프로젝트 이미지 10~18번에서 옮겨 적음)",
        "각주": NOTES,
        "fields": FIELDS,
        "counts": {
            "colors": len(colors),
            "colors_KS직접값": sum(1 for x in colors if x["value_from"] == "KS 직접"),
            "colors_대응관용색값": sum(1 for x in colors if x["value_from"] == "대응 관용색"),
            "colors_색역밖조정": sum(1 for x in colors if x["gamut_mapped"]),
            "substitutes": len(substitutes),
            "substitutes_약한대체": sum(1 for x in substitutes if x["substitute_weak"]),
        },
        "colors": colors,
        "substitutes": substitutes,
    }


# ---------------------------------------------------------------- 파일 안 설명
NOTES = [
    "[1] 이 파일의 이름은 전부 KS A 0011:2015 표 7(계통색이름)·표 8(관용색이름)에 있는 이름이다. KS 에 없는 이름은 들어 있지 않다.",
    "[2] colors 는 실제로 칩·그림에 쓰는 색이다(값이 있는 이름만). 매칭·파생·칩 표시는 이 목록 안에서만 일어난다.",
    "[3] substitutes 는 KS 에 이름은 있지만 KS 가 색 값을 주지 않은 계통색이다(표 7 은 색상 범위만, 부도는 영역만 제시). "
    "이 이름은 색으로 쓰지 않고 use_instead 의 이름으로 바꿔 쓴다(팀 결정 방안 가).",
    "[4] substitutes 를 남겨 두는 이유: (a) 팀장님 규칙·기억 질감 파생(예: 아련한 → '연한 청록')에서 이 이름들이 실제로 나오므로 "
    "바꿔 쓸 이름을 바로 찾기 위해, (b) 'KS 이름이지만 값 없음'과 'KS 에 없는 이름(오타·비표준)'을 구분하기 위해. "
    "어느 쪽에도 없는 이름은 오류로 처리한다.",
    "[5] 대체 순서: 같은 조합색 안에서 톤이 가까운 이름 → 없으면 같은 기본색에서 톤이 가까운 이름. "
    "substitute_weak=true 는 톤이 크게 달라진 대체(예: 연한 청록 → 청록)로, 이름 매칭에는 쓰되 기억 질감 파생색으로는 쓰지 않는다.",
    "[6] 색 값의 출처(value_from): 'KS 직접' = 표 8 이 그 이름에 준 먼셀값. "
    "'대응 관용색' = KS 가 그 계통색에 값을 주지 않아, 표 8 에서 그 계통색에 대응하는 관용색의 먼셀값을 규칙으로 골라 붙인 것"
    "(rep_rule: same_name=이름이 같은 관용색, only=대응 관용색이 하나, medoid=여럿 중 다른 것들과 색차 합이 가장 작은 것).",
    "[7] KS 가 주는 것은 먼셀값까지다. hex·LCh 는 이 스크립트가 먼셀값에서 계산했다: "
    "Munsell Renotation(C 광원) → XYZ → Bradford C→D65 → CIELAB/LCh(D65), sRGB hex.",
    "[8] 화면(sRGB)에 담기지 않는 색(gamut_mapped=true)은 명도 L·색상 h 를 그대로 두고 채도 C 만 줄였다. "
    "lch_munsell 이 원래 값, lch 가 실제 화면색 값이며 둘의 차이는 de_mapped(ΔE00).",
    "[9] 표 8 원문 각주: 대표값은 연상 색의 합의반응·최빈값이며 색 영역의 중심값이 아니고 유일한 대표성을 갖지 않는 보조 참고자료다. "
    "MaC 는 '같은 이름 → 항상 같은 색'을 위해 이 값을 고정 기준으로 쓴다(KS 규정이 아니라 팀 규칙).",
    "[10] KS 원문 그대로라서 표 7 범위와 어긋나는 3건: 진주색(10YR 9/1, 분홍빛 하양 10P-7.5YR), "
    "은회색(N8.5, 밝은 회색 N7-N8), 쥐색(N4.25, 어두운 회색 N3-N4). 옮겨 적기 오류 아님.",
    "[11] 관용색 이름의 괄호·대괄호(감색[과일], 검정(검은색) 등)는 KS 표기 그대로다. 화면 표시 때 뗄지는 팀이 정한다.",
    "[12] KS 에서 같은 먼셀값을 받은 관용색끼리는 hex 도 같다(예: 개나리색=노랑). 면적 계산 때 같은 색으로 합쳐진다.",
]

FIELDS = {
    "colors[].kind": "관용 / 계통 / 계통+관용(두 표에 같은 이름) [1]",
    "colors[].value_from": "KS 직접 / 대응 관용색 [6]",
    "colors[].munsell": "KS 먼셀값 [6]",
    "colors[].hex": "화면색. 그림·칩에 들어가는 값 [7][8]",
    "colors[].lch": "{L,C,h} hex 와 같은 색의 LCh. 면적·파생·해시 계산은 이 값을 쓴다 [7]",
    "colors[].lch_munsell": "{L,C,h} 먼셀값 그대로의 LCh [8]",
    "colors[].gamut_mapped / de_mapped": "채도를 줄였는지 / 줄여서 생긴 색차 [8]",
    "colors[].systematic": "(관용색) 표 8 의 대응 계통색이름",
    "colors[].rep_from / rep_rule / rep_candidates": "(계통색) 값을 가져온 관용색 / 고른 규칙 / 후보 [6]",
    "colors[].hue_range": "(계통색) 표 7 색상 범위",
    "substitutes[].use_instead": "대신 쓸 이름 (반드시 colors 안의 이름) [3][5]",
    "substitutes[].substitute_rule": "같은 조합색 / 같은 기본색 [5]",
    "substitutes[].substitute_weak": "톤이 크게 달라진 대체 → 파생색으로 쓰지 않음 [5]",
}


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}  {data['version']}  hash={data['content_hash']}")
    print(json.dumps(data["counts"], ensure_ascii=False))
