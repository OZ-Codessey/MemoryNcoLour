"""palette.py — 매칭된 색들로 면적을 나누고, 그림 팔레트와 칩 4개를 만든다 (규칙 C·D·E).

C. 면적
   · 축 가중치 = 팀장님 COLOR_MAPPING_PRIORITY (prompts/color_association.py 에서 읽음)
     emotion .15 · memory_color .20 · time .10 · space .15 · temperature .10 · light .20 · sensory .10
   · 색이 하나도 안 잡힌 축은 빼고 남은 축끼리 합이 1이 되게 다시 나눈다
   · 한 축에 단어가 여럿이면 그 축 면적을 똑같이 나눈다
   · 같은 색(같은 hex — KS 에서 같은 먼셀값을 받은 이름 포함)은 하나로 합친다
D. 기억 질감 → 파생색 방향 (면적은 없음)
   · 아련한 → 연한(없으면 흐린) · 생생한 → 선명한(없으면 진한) · 소중한 → 밝은(없으면 흐린)
   · 없음 → 밝은(없으면 흐린)   ← 팀 규칙에 없는 경우라 정한 기본값
   · 표 7 에 그 수식어 이름이 없거나 값이 없으면 같은 방향에 가까운 다음 수식어(NEXT_MODS),
     그래도 없으면 같은 기본색 계열에서 방향에 맞는 색 중 가장 가까운 KS 색
   · 값 없는 이름의 '약한 대체'(ks_colors substitutes.substitute_weak)는 파생색으로 쓰지 않는다
E. 팔레트와 칩
   · 그림 팔레트 = 뽑힌 색 N개 전부 (+ N<4 이면 파생색 4−N개)
   · 파생색은 면적이 큰 부모부터 한 개씩 돌아가며 만든다(N=1 이면 한 부모에서 3개)
     파생색 하나는 그 순간 부모 면적의 1/3 을 그림에서만 가져간다 (칩 순위는 파생 전 면적으로 정함)
   · 칩 4개 = 파생 전 면적 큰 순 → 같으면 축 순서(emotion→memory_color→time→space→temperature→light→sensory) → 이름순,
     파생색은 항상 맨 뒤. 역할·표시 비율: 주조 45 · 보조 30 · 분위기 15 · 강조 10
   · N=0 이면 색을 정할 수 없음 → 다시 입력 요청
"""
from __future__ import annotations

import ast
import math
from pathlib import Path

from .data import AXES, ROOT, Corpus

DEFAULT_WEIGHTS = {"emotion": 0.15, "memory_color": 0.20, "time": 0.10, "space": 0.15,
                   "temperature": 0.10, "light": 0.20, "sensory": 0.10}
ROLES = [("주조", "dominant", 0.45), ("보조", "supporting", 0.30), ("분위기", "atmospheric", 0.15), ("강조", "accent", 0.10)]
DIRECTION = {"아련한": ["연한", "흐린"], "생생한": ["선명한", "진한"], "소중한": ["밝은", "흐린"], "없음": ["밝은", "흐린"]}
# 첫 두 수식어가 없을 때 이어서 시도할 순서 — 같은 방향(아련=옅게, 생생=진하게, 소중=밝게)에 가까운 톤부터
NEXT_MODS = {"아련한": ["밝은", "탁한", "선명한", "진한", "어두운"],
             "생생한": ["밝은", "어두운", "탁한", "흐린", "연한"],
             "소중한": ["연한", "선명한", "탁한", "진한", "어두운"],
             "없음": ["연한", "선명한", "탁한", "진한", "어두운"]}
NEUTRAL_LADDER = ["하양", "밝은 회색", "회색", "어두운 회색", "검정"]


class NoColors(Exception):
    """문장에서 색을 하나도 정하지 못함 → 사용자에게 다시 입력을 요청."""


def team_weights() -> dict:
    """팀장님 가중치를 원본 파일에서 읽는다 (실행하지 않고 값만). 못 읽으면 같은 값의 기본표."""
    p = ROOT / "prompts" / "color_association.py"
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "COLOR_MAPPING_PRIORITY":
                w = ast.literal_eval(node.value)
                if set(w) == set(AXES):
                    return {k: float(v) for k, v in w.items()}
    except Exception:
        pass
    return dict(DEFAULT_WEIGHTS)


def _lab(c: dict) -> tuple[float, float, float]:
    L, C, h = c["lch"]["L"], c["lch"]["C"], math.radians(c["lch"]["h"])
    return L, C * math.cos(h), C * math.sin(h)


def _dist(a: dict, b: dict) -> float:
    return math.dist(_lab(a), _lab(b))


# ---------------------------------------------------------------- C. 면적
def allocate(matches, corpus: Corpus) -> list[dict]:
    weights = team_weights()
    by_axis: dict[str, list] = {}
    for m in matches:
        if m.ok and m.axis in weights:
            by_axis.setdefault(m.axis, []).append(m)
    if not by_axis:
        raise NoColors("색을 정할 수 있는 단어가 없음")
    total = sum(weights[a] for a in by_axis)
    merged: dict[str, dict] = {}
    for axis in AXES:                                   # 축 순서대로 → 처음 나온 축 = 동점 처리 기준
        for m in by_axis.get(axis, []):
            c = corpus.color(m.color)
            area = weights[axis] / total / len(by_axis[axis])
            e = merged.setdefault(c["hex"], {"hex": c["hex"], "area": 0.0, "axis_rank": AXES.index(axis),
                                              "sources": [], "_names": {}})
            e["area"] += area
            n = e["_names"].setdefault(c["name"], [0.0, AXES.index(axis)])
            n[0] += area
            e["sources"].append({"text": m.text, "axis": axis, "method": m.method, "word_id": m.word_id,
                                 "color": c["name"], "area": round(area, 6)})
    entries = []
    for e in merged.values():
        # 같은 hex 에 KS 이름이 여럿이면(예: 개나리색=노랑) 면적을 가장 많이 준 이름 → 먼저 나온 축 → 이름순
        e["name"] = min(e.pop("_names").items(), key=lambda kv: (-kv[1][0], kv[1][1], kv[0]))[0]
        e["area"] = round(e["area"], 6)
        entries.append(e)
    entries.sort(key=lambda e: (-e["area"], e["axis_rank"], e["name"]))
    return entries


# ---------------------------------------------------------------- D. 파생색
def _family(c: dict) -> tuple[str, str]:
    return c.get("base", ""), c.get("combo", "")


def derive_options(parent: dict, texture: str, corpus: Corpus) -> list[dict]:
    """부모 색에서 만들 수 있는 파생색 후보를 우선순위대로 (KS 값이 있는 이름만)."""
    base, combo = _family(parent)
    if base in ("하양", "회색", "검정"):
        names = [n for n in NEUTRAL_LADDER if n in corpus.colors]
        here = parent["name"] if parent["name"] in names else {"하양": "하양", "회색": "회색", "검정": "검정"}[base]
        i = names.index(here) if here in names else 0
        lighter, darker = names[:i][::-1], names[i + 1:]
        order = darker + lighter if texture == "생생한" else lighter + darker
        return [corpus.colors[n] for n in order]

    systematic = {}
    for c in corpus.colors.values():
        if c["kind"] in ("계통", "계통+관용"):
            systematic[(c["base"], c["combo"], c["modifier"])] = c
    weak = {}
    for s in corpus.substitutes.values():
        weak[(s["base"], s["combo"], s["modifier"])] = s

    mods = DIRECTION.get(texture, DIRECTION["없음"]) + NEXT_MODS.get(texture, NEXT_MODS["없음"])
    out = []
    for mod in mods:
        for key in ((base, combo, mod), (base, "", mod)):
            if key in systematic:
                out.append(systematic[key])
            elif key in weak and not weak[key]["substitute_weak"]:
                out.append(corpus.colors[weak[key]["use_instead"]])
    # 마지막: 같은 기본색 계열에서 — 질감 방향에 맞는 색(아련=더 밝고 덜 진함 / 생생=더 진함 / 그 밖=더 밝음) 먼저, 그 안에서 가까운 순
    pL, pC = parent["lch"]["L"], parent["lch"]["C"]

    def fits(c):
        L, C = c["lch"]["L"], c["lch"]["C"]
        if texture == "아련한":
            return L >= pL and C <= pC
        if texture == "생생한":
            return C >= pC
        return L >= pL
    same_base = sorted((c for c in corpus.colors.values() if c.get("base") == base),
                       key=lambda c: (not fits(c), _dist(c, parent), c["name"]))
    out += same_base
    seen, uniq = set(), []
    for c in out:
        if c["hex"] not in seen:
            seen.add(c["hex"])
            uniq.append(c)
    return uniq


# ---------------------------------------------------------------- E. 팔레트·칩
def build_palette(matches, texture: str, corpus: Corpus) -> dict:
    texture = texture if texture in DIRECTION else "없음"
    entries = allocate(matches, corpus)            # 파생 전 면적 큰 순
    paint = [{"name": e["name"], "hex": e["hex"], "area": e["area"], "derived": False} for e in entries]
    used = {e["hex"] for e in entries}
    derived = []
    need = max(0, 4 - len(entries))
    parent_i, guard = 0, 0
    while need > 0 and guard < 50:
        guard += 1
        pe = entries[parent_i % len(entries)]
        parent_c = corpus.color(pe["name"])
        pick = next((c for c in derive_options(parent_c, texture, corpus) if c["hex"] not in used), None)
        if pick is not None:
            p = next(x for x in paint if x["hex"] == pe["hex"])
            share = p["area"] / 3
            p["area"] -= share
            used.add(pick["hex"])
            d = {"name": pick["name"], "hex": pick["hex"], "area": share, "derived": True,
                 "parent": pe["name"], "direction": texture}
            paint.append(d)
            derived.append(d)
            need -= 1
        parent_i += 1
        if pick is None and parent_i >= len(entries) * 4:
            break
    if len(entries) + len(derived) < 4:
        raise NoColors("칩 4개를 채울 파생색을 찾지 못함")

    order = [("entry", e) for e in entries] + [("derived", d) for d in derived]
    chips = []
    for (kind, x), (ko, en, w) in zip(order[:4], ROLES):
        c = corpus.color(x["name"])
        if kind == "entry":
            words = ", ".join(f"{s['text']}" for s in x["sources"])
            reason = f"{words}에서 온 색"
        else:
            reason = f"'{x['parent']}'에서 기억 질감({x['direction']})에 맞춰 이어진 색"
        chips.append({"role": ko, "role_en": en, "color_name": c["name"], "name": c["name"], "hex": c["hex"],
                      "weight": w, "area_ratio": w, "lch": c["lch"], "munsell": c.get("munsell"),
                      "derived": kind == "derived", "reason": reason})
    for p in paint:
        p["area"] = round(p["area"], 6)
    paint.sort(key=lambda p: (-p["area"], p["derived"], p["name"]))
    return {"entries": entries, "paint_palette": paint, "chips": chips, "texture": texture,
            "n_extracted": len(entries), "n_derived": len(derived)}
