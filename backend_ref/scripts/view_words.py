"""view_words.py — words.json 을 눈으로 확인하는 HTML 한 장을 만든다 (검토용, 저장소에 넣지 않아도 됨).
실행  python scripts/view_words.py [출력경로]
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
d = json.loads((ROOT / "data" / "kscorpus" / "words.json").read_text(encoding="utf-8"))
out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "kscorpus" / "words_view.html"
AX = d["axes"]
order = list(AX)

cards = []
for w in d["words"]:
    chips = "".join(f"<span class=chip title='{html.escape(c['name'])} {c['hex']}' style='background:{c['hex']}'></span>"
                    f"<span class=cn>{html.escape(c['name'])}</span>" for c in w["colors"]) or "<span class=cn>색 없음</span>"
    src = " ".join(f"<span class='tag t{i}'>{html.escape(s)}</span>" for i, s in enumerate(w["sources"]))
    al = html.escape(", ".join(w["aliases"]))
    note = html.escape(w.get("note", ""))
    cards.append(f"<div class=row data-axis={w['axis']} data-src='{html.escape(' '.join(w['sources']))}' "
                 f"data-q='{html.escape(w['word'] + ' ' + ' '.join(w['aliases']) + ' ' + ' '.join(c['name'] for c in w['colors']))}'>"
                 f"<div class=w><b>{html.escape(w['word'])}</b> <small>{AX[w['axis']]} · {w['mode']}</small></div>"
                 f"<div class=c>{chips}</div><div class=m>{src}<br><small>{al}</small>"
                 f"{'<br><small class=note>' + note + '</small>' if note else ''}</div></div>")
ex = "".join(f"<span class=ex><span class=chip style='background:{e['hex']}'></span>{html.escape(e['text'])} → {html.escape(e['ks'])}</span>"
             for e in d["explicit_colors"] if e["source"] == "일상 표현")
tabs = "".join(f"<button data-a={a}>{AX[a]} <small>{d['counts']['by_axis'].get(a, 0)}</small></button>" for a in order)
page = f"""<!doctype html><html lang=ko><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>단어 코퍼스 확인</title>
<style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#e4e2dc;--card:#fff;--acc:#2f5d8a}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#18181a;--fg:#ececea;--mut:#a3a39e;--line:#33332f;--card:#212124;--acc:#8db4dc}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}}
header{{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px;z-index:2}}
h1{{font-size:17px;margin:0 0 4px}}p.s{{margin:0 0 8px;color:var(--mut);font-size:12.5px}}
.tabs{{display:flex;gap:6px;flex-wrap:wrap}}button{{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:4px 10px;font:inherit;font-size:13px;cursor:pointer}}
button.on{{background:var(--acc);color:#fff;border-color:var(--acc)}}button.on small{{color:#fff}}input{{width:100%;margin-top:8px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}}
main{{padding:8px 16px 40px;max-width:1100px}}.row{{display:grid;grid-template-columns:170px 1fr 1.3fr;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);align-items:start}}
@media (max-width:640px){{.row{{grid-template-columns:1fr}}}}
.chip{{display:inline-block;width:22px;height:22px;border-radius:5px;border:1px solid #8884;vertical-align:middle;margin-right:4px}}.cn{{margin-right:10px;font-size:13px}}
small{{color:var(--mut)}}.tag{{font-size:11.5px;padding:1px 7px;border-radius:999px;border:1px solid var(--line);margin-right:4px}}.note{{font-style:italic}}
.ex{{display:inline-flex;align-items:center;margin:3px 10px 3px 0;font-size:13px}}details{{margin:14px 0}}
</style>
<header><h1>단어 코퍼스 — {d['version']} ({d['content_hash']})</h1>
<p class=s>단어 {d['counts']['words']}개 · 표기 {d['counts']['surfaces']}개 · 명시 색 표현 {d['counts']['explicit_colors']}개 · 모든 색은 KS A 0011 이름 (ks_colors {d['ks_colors_hash']})</p>
<div class=tabs><button data-a=all class=on>전체</button>{tabs}</div>
<input id=q placeholder='단어·별칭·색 이름으로 찾기'></header>
<main><details><summary>명시 색 표현 (일상 표현 → KS 이름)</summary>{ex}</details><div id=list>{''.join(cards)}</div></main>
<script>
let ax='all';const rows=[...document.querySelectorAll('.row')],q=document.getElementById('q');
function f(){{const t=q.value.trim();rows.forEach(r=>{{r.style.display=((ax==='all'||r.dataset.axis===ax)&&(!t||r.dataset.q.includes(t)))?'':'none'}})}}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));b.classList.add('on');ax=b.dataset.a;f()}});
q.oninput=f;
</script></html>"""
out.write_text(page, encoding="utf-8")
print(out)
