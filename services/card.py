"""
MaC — 결과 카드 HTML (사이트에 나올 최종 결과물 시안)
  그림 + 한 줄 요약 + COLOR RATIO 막대 + 칩 4개(영문 이름 · HEX · 비율 · 이유) + 표본 코드(코드 보기)
  칩 비율은 그림에서 잰 실제 값 (4색 합 100%).
"""
from __future__ import annotations

import html


def render_card(summary: str, chips: list[dict], specimen_hash: str, painting_src: str = "painting.png",
                sentence: str | None = None) -> str:
    pct = [round(c["ratio"] * 100) for c in chips]
    if pct:
        pct[0] += 100 - sum(pct)                       # 표시 비율 합을 정확히 100 으로
    bar = "".join(f'<span style="flex:{p};background:{c["hex"]}" title="{c["hex"]} {p}%"></span>'
                  for c, p in zip(chips, pct))
    rows = ""
    for c, p in zip(chips, pct):
        name = html.escape(c["color_name"]) if c.get("color_name") else c["hex"]
        hex_cell = f'<code>{c["hex"]}</code>' if c.get("color_name") else ""   # 이름이 있으면 HEX 는 옆에
        reason = f'<p>{html.escape(c["reason"])}</p>' if c.get("reason") else ""
        rows += (f'<li><i style="background:{c["hex"]}"></i><div class="t"><div class="h"><b>{name}</b>'
                 f'<span>{hex_cell}<em>{p}%</em></span></div>{reason}</div></li>')
    src_line = f'<p class="src">{html.escape(sentence)}</p>' if sentence else ""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>MaC Specimen</title>
<style>
:root{{--bg:#ece8e2;--card:#fbfaf7;--fg:#1f1b18;--mute:#8a817a;--line:#e7e2db;--chip:#fff}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#161412;--card:#211e1b;--fg:#f1ede8;--mute:#a39a92;--line:#38332e;--chip:#2a2622}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.6 'Pretendard','Apple SD Gothic Neo','Malgun Gothic',-apple-system,sans-serif;display:flex;justify-content:center;padding:32px 16px}}
.card{{width:100%;max-width:560px;background:var(--card);border-radius:24px;overflow:hidden;box-shadow:0 20px 60px #0002}}
.top{{display:flex;justify-content:space-between;align-items:center;padding:22px 28px 18px;border-bottom:1px solid var(--line)}}
.top h1{{margin:0;font-size:15px;letter-spacing:.22em;font-weight:800}}
button{{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--chip);color:var(--fg);border-radius:6px;padding:6px 12px;cursor:pointer}}
.code{{display:none;margin:0 28px;padding:10px 14px;border-radius:8px;background:var(--fg);color:var(--card);font:600 14px ui-monospace,Consolas,monospace;letter-spacing:.06em}}
.code.on{{display:block;margin-top:16px}}
.art{{padding:22px 28px 0}}.art img{{width:100%;display:block;border-radius:12px;aspect-ratio:4/5;object-fit:cover}}
.body{{padding:22px 28px 26px}}
.quote{{border-left:4px solid var(--fg);background:var(--chip);padding:16px 18px;font-weight:700;font-size:16px;border-radius:0 8px 8px 0}}
.src{{color:var(--mute);font-size:12px;margin:8px 0 0}}
.ratio{{display:flex;justify-content:space-between;margin:24px 0 8px;font-size:12px;letter-spacing:.14em;color:var(--mute);font-weight:700}}
.bar{{display:flex;height:18px;border-radius:4px;overflow:hidden}}.bar span{{display:block}}
ul{{list-style:none;margin:20px 0 0;padding:0;display:grid;gap:10px}}
li{{display:flex;gap:16px;align-items:center;border:1px solid var(--line);border-radius:12px;padding:14px 16px;background:var(--chip)}}
li i{{flex:none;width:56px;height:56px;border-radius:8px}}.t{{flex:1;min-width:0}}
.h{{display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap}}.h b{{font-size:16px}}
.h span{{display:flex;gap:8px;align-items:center}}code{{font:600 12px ui-monospace,Consolas,monospace}}
em{{font-style:normal;font-weight:700;font-size:12px;background:var(--bg);border-radius:4px;padding:1px 6px}}
li p{{margin:4px 0 0;color:var(--mute);font-size:13px}}
.foot{{display:flex;justify-content:space-between;align-items:center;border-top:1px dashed var(--line);margin-top:22px;padding-top:18px;color:var(--mute);font-size:13px}}
</style></head><body><main class="card">
<div class="top"><h1>MAC SPECIMEN</h1><button onclick="document.getElementById('code').classList.toggle('on')">코드 보기</button></div>
<div id="code" class="code">{html.escape(specimen_hash)}</div>
<div class="art"><img src="{html.escape(painting_src)}" alt="기억의 색면추상화"></div>
<div class="body"><div class="quote">"{html.escape(summary)}"</div>{src_line}
<div class="ratio"><span>COLOR RATIO</span><span>{" : ".join(f"{p}%" for p in pct)}</span></div>
<div class="bar">{bar}</div><ul>{rows}</ul>
<div class="foot"><span>칩 비율은 그림에서 잰 실제 면적입니다.</span></div></div></main></body></html>"""
