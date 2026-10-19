"""
v1 vs v2 프롬프트 비교 (텍스트 단계만 — 그림 비용 없음)

같은 문장들을 v1(prompts/color_prompt.py)과 v2(prompts/color_prompt_v2.py)에 넣고
둘 다 같은 Claude 모델로 돌려서, 프롬프트 차이만 비교한다.

실행 (MemoryNcoLour-main 에서):
    python scripts/compare_prompts.py                 # 기본 문장 10개
    python scripts/compare_prompts.py --file my.txt   # 한 줄에 한 문장 (앞에 'dark|' 'bright|' 붙이면 분위기 그룹)
    python scripts/compare_prompts.py --n 3           # 앞의 3개만 (빠른 확인)
    python scripts/compare_prompts.py --fresh         # 저장된 응답 무시하고 다시 호출
    python scripts/compare_prompts.py --dry-run       # API 없이 가짜 응답으로 화면만 확인

결과: out/compare_<시각>.html (브라우저로 열기) + out/compare_<시각>.json
같은 문장·같은 버전의 응답은 out/compare_cache.json 에 저장해 다시 실행해도 API 를 또 부르지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import itertools
import json
import math
import random
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompts import color_prompt as v1                        # noqa: E402
from prompts import color_prompt_v2 as v2                     # noqa: E402

OUT = ROOT / "out"
CACHE = OUT / "compare_cache.json"
HEX_OK = v2.HEX_RE

DEFAULT_SENTENCES = [
    ("bright", "선선한 저녁 노을 아래 한강에서 친구들과 맥주를 마시며 웃던 여유로운 순간"),
    ("bright", "초등학교 운동회 날 청군 깃발을 흔들며 목이 터져라 응원했던 기억"),
    ("bright", "첫 월급으로 엄마에게 빨간 목도리를 사 드리던 날의 뿌듯함"),
    ("bright", "봄날 벚꽃이 흩날리는 캠퍼스 벤치에서 처음 고백을 받았던 오후"),
    ("bright", "여름방학 바닷가에서 파도에 발을 담그고 수박을 나눠 먹던 날"),
    ("dark", "할머니 장례식이 끝나고 텅 빈 시골집 마루에 혼자 앉아 있던 겨울 저녁"),
    ("dark", "수능 전날 밤 잠이 오지 않아 불 꺼진 방에서 천장만 바라보던 불안한 시간"),
    ("dark", "비 오는 날 이별을 말하고 혼자 버스 정류장에 서 있던 기억"),
    ("dark", "이사 가던 날 텅 빈 방을 마지막으로 돌아보며 느낀 아련한 그리움"),
    ("dark", "친구에게 끝내 사과하지 못하고 멀어진 뒤 문득 떠오르는 후회"),
]


# ---------------------------------------------------------------- 색 계산
def hex_to_lab(hx: str):
    return v2._hex_to_lab(hx)


def lch(hx: str):
    L, a, b = hex_to_lab(hx)
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def de(a: str, b: str) -> float:
    return v2._de76(a, b)


def colors_of(version: str, data) -> list[tuple[str, float]]:
    """(hex, 면적) 목록. v1 은 weight, v2 는 share."""
    if not data:
        return []
    if version == "v1":
        return [(c["hex"], float(c.get("weight", 0.25))) for c in data.get("palette", [])
                if isinstance(c, dict) and HEX_OK.match(str(c.get("hex", "")).upper())]
    return [(c["hex"], c["share"]) for c in data.get("palette", [])]


def stats(cols):
    if not cols:
        return None
    tot = sum(w for _, w in cols) or 1
    L = sum(lch(h)[0] * w for h, w in cols) / tot
    C = sum(lch(h)[1] * w for h, w in cols) / tot
    warm = sum(w for h, w in cols if lch(h)[1] > 10 and (lch(h)[2] < 100 or lch(h)[2] > 330)) / tot
    spread = max(lch(h)[0] for h, _ in cols) - min(lch(h)[0] for h, _ in cols)
    return {"L": round(L, 1), "C": round(C, 1), "warm": round(warm, 2), "L_spread": round(spread, 1)}


def palette_distance(a, b) -> float:
    """두 팔레트가 얼마나 다른지: 각 색에서 상대 팔레트의 가장 가까운 색까지 거리를 면적으로 평균 (양방향)."""
    def one(x, y):
        tot = sum(w for _, w in x) or 1
        return sum(w * min(de(h, h2) for h2, _ in y) for h, w in x) / tot
    return (one(a, b) + one(b, a)) / 2


# ---------------------------------------------------------------- 호출
def call_v1(sentence: str):
    from services.claude_client import claude_json
    prompt = f"{v1.build_system_prompt()}\n\n[입력된 사용자 기억 사연]\n\"{sentence}\""   # analyze.py 와 같은 방식
    return claude_json(prompt)


def call_v2(sentence: str):
    from services.claude_client import claude_json
    return claude_json(v2.build_user_prompt(sentence), system=v2.build_system_prompt(), schema=v2.RESPONSE_SCHEMA)


def fake(version: str, sentence: str):
    rnd = random.Random(hashlib.md5((version + sentence).encode()).hexdigest())
    rh = lambda: "#%02X%02X%02X" % tuple(rnd.randrange(30, 240) for _ in range(3))   # noqa: E731
    if version == "v1":
        return {"memory_summary": "가짜 요약", "palette": [
            {"color_name": f"Fake {i}", "hex": rh(), "weight": w, "reason": "가짜"}
            for i, w in enumerate((0.45, 0.30, 0.15, 0.10))]}
    roles = ["dominant", "dominant", "supporting", "supporting", "atmospheric", "atmospheric", "accent"]
    shares = [0.25, 0.20, 0.17, 0.13, 0.08, 0.07, 0.10]
    return {"memory_summary": "가짜 요약", "dominant_emotion": "calm_peace", "texture": "소중한",
            "palette": [{"role": r, "color_name": f"Fake {i}", "hex": rh(), "share": s, "reason": "가짜"}
                        for i, (r, s) in enumerate(zip(roles, shares))],
            "painting": {"medium": "oil", "energy": "calm", "mood_keywords": ["fake", "quiet", "soft"]}}


def run_one(version, sentence, cache, fresh, dry):
    # v2 는 프롬프트 버전을 키에 넣는다 → 프롬프트를 고치면 v2 만 다시 호출하고 v1 은 저장된 응답을 쓴다
    key = f"{version}|{sentence}" if version == "v1" else f"{version}|{v2.PROMPT_VERSION}|{sentence}"
    if dry:
        return {"raw": fake(version, sentence), "seconds": 0.0, "error": None, "cached": False}
    if not fresh and key in cache:
        return {**cache[key], "cached": True}
    t0 = time.time()
    try:
        raw, err = (call_v1 if version == "v1" else call_v2)(sentence), None
    except Exception as e:
        raw, err = None, f"{type(e).__name__}: {str(e)[:200]}"
    rec = {"raw": raw, "seconds": round(time.time() - t0, 1), "error": err}
    if raw is not None:
        cache[key] = rec
    return {**rec, "cached": False}


def check(version, rec):
    """형식 검사. v1 은 analyze.py 와 같은 기준(정확히 4개·대문자 HEX), v2 는 validate_and_normalize."""
    if rec["raw"] is None:
        return False, [rec["error"]], None
    if version == "v1":
        pal = rec["raw"].get("palette") if isinstance(rec["raw"], dict) else None
        ok = isinstance(pal, list) and len(pal) == 4 and all(
            isinstance(c, dict) and HEX_OK.match(str(c.get("hex", "")).strip().upper()) for c in pal)
        return ok, ([] if ok else ["palette 가 정확히 4개·HEX 형식이 아님"]), rec["raw"]
    ok, errors, plan = v2.validate_and_normalize(rec["raw"])
    return ok, errors, plan


# ---------------------------------------------------------------- 화면
def chips_html(version, data):
    if not data:
        return "<div class='err'>결과 없음</div>"
    pal = data.get("palette", [])
    bar = "".join(f"<span style='flex:{(c.get('weight') if version=='v1' else c['share']) or 0.01};background:{c['hex']}'></span>"
                  for c in pal if HEX_OK.match(str(c.get("hex", "")).upper()))
    rows = "".join(
        f"<li><i style='background:{html.escape(str(c.get('hex','')))}'></i>"
        f"<b>{html.escape(str(c.get('color_name','')))}</b> <code>{html.escape(str(c.get('hex','')))}</code> "
        f"<small>{round(100*float((c.get('weight') if version=='v1' else c['share']) or 0))}%"
        f"{' · ' + c['role'] if version=='v2' else ''}</small><br><span>{html.escape(str(c.get('reason','')))}</span></li>"
        for c in pal)
    meta = ""
    if version == "v2":
        p = data["painting"]
        meta = (f"<div class='meta'>{html.escape(data['dominant_emotion'])} · {html.escape(data['texture'])} · "
                f"{p['medium']} · {p['energy']} · {html.escape(', '.join(p['mood_keywords']))}</div>")
        if data.get("fixes"):
            meta += f"<div class='fix'>자동 수정: {html.escape('; '.join(data['fixes']))}</div>"
    return (f"<div class='sum'>{html.escape(str(data.get('memory_summary','')))}</div>"
            f"<div class='bar'>{bar}</div>{meta}<ul>{rows}</ul>")


def render(rows, summary, model_label, stamp):
    head = "".join(f"<tr><th>{k}</th><td>{summary['v1'][k]}</td><td>{summary['v2'][k]}</td></tr>" for k in summary["v1"])
    body = ""
    for r in rows:
        s1, s2 = r["v1"]["stats"], r["v2"]["stats"]
        fmt = lambda s: f"명도 {s['L']} · 채도 {s['C']} · 난색 {int(s['warm']*100)}%" if s else "-"   # noqa: E731
        body += (f"<section><h3><em class='{r['mood']}'>{r['mood']}</em> {html.escape(r['sentence'])}</h3>"
                 f"<div class='cols'><div><h4>v1 <small>{fmt(s1)} · {r['v1']['seconds']}s"
                 f"{'' if r['v1']['ok'] else ' · ❌ ' + html.escape('; '.join(map(str, r['v1']['errors'])))}</small></h4>"
                 f"{chips_html('v1', r['v1']['data'])}</div>"
                 f"<div><h4>v2 <small>{fmt(s2)} · {r['v2']['seconds']}s"
                 f"{'' if r['v2']['ok'] else ' · ❌ ' + html.escape('; '.join(map(str, r['v2']['errors'])))}</small></h4>"
                 f"{chips_html('v2', r['v2']['data'])}</div></div></section>")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MaC 프롬프트 비교</title><style>
:root{{--bg:#f7f5f2;--fg:#2b2725;--mute:#8c827a;--line:#e6e1da;--card:#fff}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px}}h1{{font-size:20px;margin:0 0 4px}}.sub{{color:var(--mute);margin-bottom:16px}}
table{{border-collapse:collapse;background:var(--card);margin-bottom:24px;width:100%}}th,td{{border:1px solid var(--line);padding:6px 10px;text-align:left}}
th{{font-weight:600;width:40%}}section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:14px}}
h3{{font-size:15px;margin:0 0 10px}}em{{font-style:normal;font-size:11px;padding:1px 6px;border-radius:4px;margin-right:6px;vertical-align:2px}}
em.bright{{background:#fde9c8}}em.dark{{background:#d9dee6}}.cols{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:760px){{.cols{{grid-template-columns:1fr}}}}h4{{margin:0 0 6px;font-size:13px}}h4 small{{color:var(--mute);font-weight:400}}
.bar{{display:flex;height:22px;border-radius:4px;overflow:hidden;margin:6px 0}}.bar span{{display:block}}
.sum{{font-weight:600}}.meta{{color:var(--mute);font-size:12px}}.fix{{color:#a0662b;font-size:12px}}
ul{{list-style:none;padding:0;margin:6px 0 0}}li{{padding:4px 0;border-top:1px dashed var(--line);font-size:13px}}
li i{{display:inline-block;width:14px;height:14px;border-radius:3px;vertical-align:-2px;margin-right:6px;border:1px solid #0001}}
li span{{color:var(--mute);font-size:12px}}code{{font-size:12px}}.err{{color:#b3261e}}
</style></head><body><main><h1>MaC 프롬프트 비교 — v1 vs v2</h1>
<div class="sub">{stamp} · 모델 {html.escape(model_label)} · 같은 모델로 프롬프트만 바꿔 비교 (그림 없음)</div>
<table><tr><th>지표</th><td><b>v1</b></td><td><b>v2</b></td></tr>{head}</table>{body}</main></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--n", type=int)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.file:
        sents = []
        for line in Path(a.file).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line:
                mood, _, text = line.partition("|") if "|" in line else ("-", "", line)
                sents.append((mood.strip(), text.strip()))
    else:
        sents = DEFAULT_SENTENCES
    if a.n:
        sents = sents[:a.n]

    OUT.mkdir(exist_ok=True)
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    from services.claude_client import label
    model_label = "dry-run" if a.dry_run else label()

    rows = []
    for i, (mood, s) in enumerate(sents, 1):
        row = {"mood": mood, "sentence": s}
        for ver in ("v1", "v2"):
            rec = run_one(ver, s, cache, a.fresh, a.dry_run)
            ok, errors, data = check(ver, rec)
            row[ver] = {"ok": ok, "errors": errors, "data": data, "seconds": rec["seconds"],
                        "cached": rec["cached"], "stats": stats(colors_of(ver, data)) if ok else None}
            print(f"[{i}/{len(sents)}] {ver} {'OK ' if ok else 'ERR'} {rec['seconds']}s"
                  f"{' (저장된 응답)' if rec['cached'] else ''} {'' if ok else errors}")
        rows.append(row)
        if not a.dry_run:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    def summarize(ver):
        good = [r for r in rows if r[ver]["ok"]]
        pals = [colors_of(ver, r[ver]["data"]) for r in good]
        # 색이 많을수록 '가장 가까운 색' 거리가 작아지므로, 공정하게 비교하려고 면적 상위 4색끼리만 잰다
        top4 = [sorted(p, key=lambda x: -x[1])[:4] for p in pals]
        pairs = list(itertools.combinations(top4, 2))
        div = sum(palette_distance(x, y) for x, y in pairs) / len(pairs) if pairs else 0
        grp = lambda m: [r[ver]["stats"]["L"] for r in good if r["mood"] == m]   # noqa: E731
        avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else "-"             # noqa: E731
        return {
            "형식 통과": f"{len(good)} / {len(rows)}",
            "평균 색 수": avg([len(p) for p in pals]),
            "문장 간 팔레트 차이 (상위 4색 기준, 클수록 문장마다 색이 다름)": round(div, 1),
            "밝은 문장 평균 명도": avg(grp("bright")),
            "어두운 문장 평균 명도": avg(grp("dark")),
            "밝은 문장 난색 비율": avg([r[ver]["stats"]["warm"] for r in good if r["mood"] == "bright"]),
            "어두운 문장 난색 비율": avg([r[ver]["stats"]["warm"] for r in good if r["mood"] == "dark"]),
            "평균 응답 시간(초, 새 호출만)": avg([r[ver]["seconds"] for r in rows if not r[ver]["cached"] and r[ver]["seconds"]]),
        }

    summary = {"v1": summarize("v1"), "v2": summarize("v2")}
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    (OUT / f"compare_{stamp}.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
                                               encoding="utf-8")
    page = OUT / f"compare_{stamp}.html"
    page.write_text(render(rows, summary, model_label, stamp), encoding="utf-8")
    print("\n".join(f"  {k}: v1 {summary['v1'][k]} | v2 {summary['v2'][k]}" for k in summary["v1"]))
    print(f"\n결과: {page}")


if __name__ == "__main__":
    main()
