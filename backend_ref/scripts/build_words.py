"""build_words.py — 단어 코퍼스(words.json)와 검토용 표(corpus_review.xlsx)를 만든다.

입력
  data/kscorpus/ks_colors.json              KS 색이름표 (colors / substitutes)
  data/kscorpus/words_src/*.tsv              축별 단어 원본 (사람이 고치는 곳)
        파일 이름 앞부분이 축: emotion / memory_color_* / time / space / temperature / light / sensory / no_color
        한 줄 형식: 단어 | 별칭(;) | KS 색이름(;) | 메모
  data/kscorpus/words_src/team_name_map.tsv  팀장님 규칙 색 이름 → KS 이름 대응표
  prompts/color_association.py               팀장님 규칙 (핵심어·축 확인용, 읽기만 함)
  data/corpus_team/MaC_color_keywords_v4.json, data/corpus/objects.jsonl   기존 단어 자료 (출처 표시·누락 보고용, 없으면 건너뜀)
출력
  data/kscorpus/words.json
  data/kscorpus/corpus_review.xlsx
  data/kscorpus/words_report.txt

검사 (하나라도 걸리면 실패, 파일을 쓰지 않음)
  · 색 이름은 ks_colors.json 의 colors 안의 이름만. substitutes 이름이면 use_instead 로 바꾸고 기록. 그 밖의 이름은 오류
  · 한 표기(단어·별칭)는 코퍼스 전체에서 한 번만 (= 한 단어는 한 축에만)
  · 팀장님 규칙 핵심어는 팀장님이 정한 축에 있어야 함 (예외: TEAM_AXIS_OVERRIDE)
  · 팀장님 규칙 핵심어 줄의 색은 그 정서군 연상색을 대응표로 바꾼 것과 같아야 함
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KS = ROOT / "data" / "kscorpus"
SRC = KS / "words_src"
OUT_JSON = KS / "words.json"
OUT_XLSX = KS / "corpus_review.xlsx"
OUT_REPORT = KS / "words_report.txt"
TEAM_RULES = ROOT / "prompts" / "color_association.py"
MENTOR_V4 = ROOT / "data" / "corpus_team" / "MaC_color_keywords_v4.json"
MENTOR_OBJ = ROOT / "data" / "corpus" / "objects.jsonl"
LEGACY_LIST = KS / "source" / "legacy_surfaces.txt"          # 위 두 파일에서 옮겨 둔 표기 목록 (이 파일을 먼저 씀)

VERSION = "words-0.1"
AXES = ["emotion", "memory_color", "time", "space", "temperature", "light", "sensory"]
AXIS_KO = {"emotion": "감정", "memory_color": "기억색(사물)", "time": "시간", "space": "공간",
           "temperature": "온도", "light": "빛", "sensory": "감각", "none": "색 없음"}
# 팀장님 규칙의 축과 다르게 두기로 합의한 핵심어
TEAM_AXIS_OVERRIDE = {"햇살": "light"}
# 팀장님 규칙 상수 이름 → 축 (MEMORY_COLOR_RULES 는 기억 질감이라 단어 코퍼스 대상 아님)
TEAM_GROUPS = {"EMOTION_COLOR_RULES": "emotion", "TIME_COLOR_RULES": "time", "SPACE_COLOR_RULES": "space"}


# ---------------------------------------------------------------- 읽기
def split(cell: str) -> list[str]:
    return [x.strip() for x in cell.split(";") if x.strip()]


def read_rows(path: Path) -> list[tuple[int, list[str]]]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = [c.strip() for c in line.split("|")]
        cells += [""] * (4 - len(cells))
        out.append((n, cells[:4]))
    return out


def axis_of(path: Path) -> str:
    stem = path.stem
    if stem == "no_color" or stem.startswith("no_color_"):
        return "none"
    if stem == "explicit_colors":
        return "explicit"
    for a in AXES:
        if stem == a or stem.startswith(a + "_"):
            return a
    raise SystemExit(f"축을 알 수 없는 파일: {path.name}")


def load_team_rules() -> dict:
    """color_association.py 를 실행하지 않고 상수만 읽는다."""
    if not TEAM_RULES.exists():
        return {}
    tree = ast.parse(TEAM_RULES.read_text(encoding="utf-8"))
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                consts[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
    return consts


def load_mentor() -> set[str]:
    """'기존 자료' 출처 표시용 표기 목록. 묶음에 옮겨 둔 목록을 쓴다 → 멘토 파일이 없는 저장소에서도 결과가 같다."""
    if LEGACY_LIST.exists():
        return {ln.strip() for ln in LEGACY_LIST.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")}
    words = set()
    if MENTOR_V4.exists():
        words |= {r["keyword"] for r in json.loads(MENTOR_V4.read_text(encoding="utf-8"))}
    if MENTOR_OBJ.exists():
        for line in MENTOR_OBJ.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                words.add(r["name"])
                words |= set(r.get("aliases", []))
    return words


# ---------------------------------------------------------------- 만들기
def build() -> tuple[dict, list[str], list[str]]:
    ks = json.loads((KS / "ks_colors.json").read_text(encoding="utf-8"))
    colors = {c["name"]: c for c in ks["colors"]}
    subs = {s["name"]: s for s in ks["substitutes"]}
    idiom_names = [c["name"] for c in ks["colors"] if c["kind"] in ("관용", "계통+관용")]

    errors, warns = [], []

    # 팀장님 대응표
    team_map = {}
    for n, (src, dst, why, _) in read_rows(SRC / "team_name_map.tsv"):
        if dst not in colors:
            errors.append(f"team_name_map.tsv:{n} '{dst}' 은 colors 에 없는 이름")
        team_map[src] = dst

    # 팀장님 핵심어 → (축, 정서군, KS 색 목록)
    consts = load_team_rules()
    team_kw: dict[str, tuple[str, str, list[str]]] = {}
    for const, axis in TEAM_GROUPS.items():
        for group, spec in consts.get(const, {}).items():
            mapped = [team_map.get(c, c) for c in spec.get("연상색", [])]
            for c in spec.get("연상색", []):
                if c not in team_map:
                    errors.append(f"팀장님 규칙 색 '{c}'({group}) 이 team_name_map.tsv 에 없음")
            for kw in spec.get("핵심어", []):
                team_kw[kw] = (TEAM_AXIS_OVERRIDE.get(kw, axis), group, mapped)
    mentor = load_mentor()

    # 명시 색 표현 (7축과 따로): 손으로 적은 일상 표현 + KS 이름 자동
    explicit, ex_seen = [], {}
    for n, (expr, aliases, cols, note) in read_rows(SRC / "explicit_colors.tsv"):
        where = f"explicit_colors.tsv:{n}"
        cs = split(cols)
        if len(cs) != 1:
            errors.append(f"{where} '{expr}' 명시 색은 KS 이름 하나만")
            continue
        target = cs[0] if cs[0] in colors else subs.get(cs[0], {}).get("use_instead")
        if target not in colors:
            errors.append(f"{where} '{expr}' 색 '{cs[0]}' 은 KS 색이름표에 없음")
            continue
        for s in [expr] + split(aliases):
            if s in ex_seen:
                errors.append(f"{where} 명시 색 표현 '{s}' 이(가) {ex_seen[s][0]} 에도 있음")
                continue
            ex_seen[s] = (where, target)
            explicit.append({"text": s, "ks": target, "source": "일상 표현", **({"note": note} if note else {})})

    import re as _re
    targets = [(c["name"], c.get("alt", ""), c["name"]) for c in ks["colors"]] + \
              [(x["name"], x.get("alt", ""), x["use_instead"]) for x in ks["substitutes"]]
    auto = [(s, t) for name, alt, t in targets for s in ([name] + ([alt] if alt else []))]   # 정확한 이름이 먼저
    for name, _, t in targets:                                     # 하양(흰색) → 하양, 흰색 (정확한 이름이 없을 때만)
        m = _re.fullmatch(r"([가-힣 ]+)\(([가-힣 ]+)\)", name)
        if m:
            auto += [(m.group(1), t), (m.group(2), t)]
    for s, target in auto:
        if s in ex_seen:
            if ex_seen[s][1] != target and ex_seen[s][0] != "KS":
                errors.append(f"{ex_seen[s][0]} 명시 색 '{s}' → {ex_seen[s][1]} 인데 KS 이름표로는 {target} — KS 쪽에 맞출 것")
            continue
        ex_seen[s] = ("KS", target)
        explicit.append({"text": s, "ks": target, "source": "KS 이름"})
    for e in explicit:
        e["hex"] = colors[e["ks"]]["hex"]

    # 원본 읽기
    files = sorted(p for p in SRC.glob("*.tsv") if p.name not in ("team_name_map.tsv", "explicit_colors.tsv"))
    rows, seen = [], {}
    for path in files:
        axis = axis_of(path)
        for n, (word, aliases, cols, note) in read_rows(path):
            where = f"{path.name}:{n}"
            if not word:
                errors.append(f"{where} 단어가 비어 있음")
                continue
            names, subst = [], []
            for c in split(cols):
                if c in colors:
                    names.append(c)
                elif c in subs:
                    names.append(subs[c]["use_instead"])
                    subst.append(f"{c}→{subs[c]['use_instead']}")
                else:
                    errors.append(f"{where} '{word}' 색 '{c}' 은 KS 색이름표에 없음")
            names = list(dict.fromkeys(names))                         # 순서 유지 중복 제거
            if axis != "none" and not names:
                errors.append(f"{where} '{word}' 색이 없음 (색이 없는 단어는 no_color.tsv 로)")
            if axis == "none" and names:
                errors.append(f"{where} '{word}' no_color 인데 색이 있음")
            surfaces = list(dict.fromkeys([word] + split(aliases)))
            for s in surfaces:
                if s in seen:
                    errors.append(f"{where} '{s}' 이(가) {seen[s]} 에도 있음 — 한 표기는 한 곳에만")
                else:
                    seen[s] = where
            rows.append({"axis": axis, "word": word, "aliases": surfaces[1:], "colors": names,
                         "note": note, "substituted": subst, "_where": where, "_file": path.name})

    # 출처 표시 + 팀장님 핵심어 검사
    found_team = set()
    for r in rows:
        surf = [r["word"]] + r["aliases"]
        src = []
        hits = [s for s in surf if s in team_kw]
        if hits:
            src.append("팀장님 규칙")
            for s in hits:
                found_team.add(s)
                t_axis, group, t_cols = team_kw[s]
                if r["axis"] != t_axis:
                    errors.append(f"{r['_where']} 팀장님 핵심어 '{s}' 는 {t_axis}({group}) 축이어야 함 — 지금 {r['axis']}")
                if r["colors"][:len(t_cols)] != t_cols:
                    errors.append(f"{r['_where']} 팀장님 핵심어 '{s}' 색은 {t_cols} 로 시작해야 함 — 지금 {r['colors']}")
            r["team_group"] = team_kw[hits[0]][1]
        if any(any(n.startswith(s) and n[len(s):].startswith("색") for n in idiom_names) for s in surf) or \
           any(n in r["colors"] and n.replace("색", "") in surf for n in idiom_names):
            src.append("KS 관용색")
        if any(s in mentor for s in surf):
            src.append("기존 자료")
        if not src:
            src.append("초안")
        r["sources"] = src

    missing_team = sorted(set(team_kw) - found_team)
    for kw in missing_team:
        errors.append(f"팀장님 핵심어 '{kw}' 이(가) 코퍼스에 없음")

    # 기존 자료 중 아직 안 들어온 표기 (오류 아님, 보고만)
    missing_mentor = sorted(w for w in mentor if w not in seen)

    # 정리
    order = {a: i for i, a in enumerate(AXES + ["none"])}
    rows.sort(key=lambda r: (order[r["axis"]], r["_file"], int(r["_where"].split(":")[1])))
    words = []
    for r in rows:
        mode = "없음" if not r["colors"] else ("고유" if len(r["colors"]) == 1 else "맥락")
        w = {"id": f"{r['axis']}:{r['word']}", "axis": r["axis"], "word": r["word"], "aliases": r["aliases"],
             "mode": mode, "colors": [{"name": n, "hex": colors[n]["hex"]} for n in r["colors"]],
             "sources": r["sources"], "review": "검토 전"}
        if r.get("team_group"):
            w["team_group"] = r["team_group"]
        if r["substituted"]:
            w["substituted"] = r["substituted"]
        if r["note"]:
            w["note"] = r["note"]
        words.append(w)

    blob = json.dumps([words, explicit], ensure_ascii=False, sort_keys=True).encode()
    surf_count = sum(1 + len(w["aliases"]) for w in words)
    data = {
        "version": VERSION,
        "content_hash": hashlib.sha256(blob).hexdigest()[:12],
        "ks_colors_hash": ks["content_hash"],
        "각주": NOTES,
        "axes": {a: AXIS_KO[a] for a in AXES + ["none"]},
        "counts": {
            "words": len(words),
            "surfaces": surf_count,
            "by_axis": dict(Counter(w["axis"] for w in words)),
            "by_mode": dict(Counter(w["mode"] for w in words)),
            "by_source": dict(Counter(s for w in words for s in w["sources"])),
            "substituted_rows": sum(1 for w in words if "substituted" in w),
            "explicit_colors": len(explicit),
        },
        "words": words,
        "explicit_colors": explicit,
    }
    report = [
        f"{VERSION} hash={data['content_hash']} ks={ks['content_hash']}",
        json.dumps(data["counts"], ensure_ascii=False),
        f"팀장님 핵심어 {len(team_kw)}개 중 코퍼스에 있음 {len(found_team)}",
        f"기존 자료(멘토 v4·objects) 표기 {len(mentor)}개 중 아직 없는 것 {len(missing_mentor)}개:",
        "  " + ", ".join(missing_mentor),
    ]
    return data, errors, report


NOTES = [
    "[1] 한 줄 = 한 단어. word 와 aliases(같은 뜻의 다른 표기)는 모두 같은 줄로 매칭된다. 임베딩은 표기마다 따로 만들어 이 줄의 id 로 이어진다.",
    "[2] axis 는 7개 축(emotion·memory_color·time·space·temperature·light·sensory) 중 하나, 또는 none(고유색 없음). 한 표기는 코퍼스 전체에서 한 축에만 있다.",
    "[3] colors 의 이름은 전부 ks_colors.json 의 colors 안에 있는 KS A 0011 이름이다. hex 는 그 표에서 옮겨 왔다(직접 적지 않음).",
    "[4] mode: 고유 = 색 하나로 정해짐 / 맥락 = 후보가 여럿이라 문장 맥락으로 고름(첫 번째가 기본) / 없음 = 고유색이 없어 색 추출에서 뺌(문장에 색이 직접 나오면 그 색).",
    "[5] sources: 팀장님 규칙 = prompts/color_association.py 의 핵심어 / KS 관용색 = KS 관용색 이름이 그 사물을 가리킴(예: 레몬 → 레몬색) / "
    "기존 자료 = 멘토 키워드 v4·objects 에 있던 표기(색은 KS 이름으로 다시 정함) / 초안 = 새로 넣은 단어.",
    "[6] 팀장님 규칙 핵심어는 팀장님이 정한 축과 정서군 연상색을 따른다. 예외: '햇살'은 합의에 따라 light. "
    "팀장님 규칙의 KS 아닌 색 이름은 words_src/team_name_map.tsv 대응표로 바꿨다(팀장님 확인 전 제안).",
    "[7] substituted: 원본에 KS 값이 없는 계통색 이름이 적혀 있어 ks_colors.json substitutes 의 대신 쓸 이름으로 바뀐 기록.",
    "[8] review: 모든 줄이 '검토 전'이다. 색 선택은 판단이 들어간 값이므로 팀 검토(corpus_review.xlsx)를 거쳐 '확정'으로 바꾼다.",
    "[9] 기억 질감(소중한·아련한·생생한)은 색을 정하는 단어가 아니라서 이 코퍼스에 넣지 않는다. Gemini 가 문장에서 따로 판단한다.",
    "[10] explicit_colors 는 문장에 색이 직접 나온 경우(규칙 A: 명시된 색 최우선)에 쓰는 표다. 7개 축과 따로 있으며 임베딩하지 않고 글자 그대로 찾는다. "
    "source: 일상 표현 = '새빨간·잿빛'처럼 손으로 적은 표현 / KS 이름 = KS 색이름 자체(값 없는 이름은 대신 쓸 이름으로 이어짐).",
]


# ---------------------------------------------------------------- 검토용 엑셀
def write_xlsx(data: dict) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "words"
    head = ["id", "축", "단어", "별칭", "방식", "색1", "", "색2", "", "색3", "", "출처", "팀장님 정서군", "메모", "검토", "의견"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for w in data["words"]:
        cols = w["colors"] + [{"name": "", "hex": ""}] * (3 - len(w["colors"]))
        row = [w["id"], AXIS_KO[w["axis"]], w["word"], ", ".join(w["aliases"]), w["mode"]]
        for c in cols[:3]:
            row += [c["name"], c["hex"]]
        row += [", ".join(w["sources"]), w.get("team_group", ""),
                (w.get("note", "") + (" / 대체: " + ", ".join(w["substituted"]) if w.get("substituted") else "")).strip(" /"),
                w["review"], ""]
        ws.append(row)
        r = ws.max_row
        for i, c in enumerate(cols[:3]):
            if c["hex"]:
                ws.cell(r, 7 + i * 2).fill = PatternFill("solid", fgColor=c["hex"][1:])
    widths = [22, 10, 14, 30, 6, 14, 9, 14, 9, 14, 9, 20, 14, 40, 8, 30]
    for i, wdt in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = wdt
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = ws.dimensions

    ns = wb.create_sheet("각주")
    for n in data["각주"]:
        ns.append([n])
    ns.column_dimensions["A"].width = 160
    for row in ns.iter_rows():
        row[0].alignment = Alignment(wrap_text=True)
    wb.save(OUT_XLSX)


if __name__ == "__main__":
    data, errors, report = build()
    if errors:
        print(f"오류 {len(errors)}건 — 파일을 쓰지 않음")
        print("\n".join("  " + e for e in errors))
        sys.exit(1)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    write_xlsx(data)
    OUT_REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report[:3]))
