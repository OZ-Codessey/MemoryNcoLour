"""
문장 1개 → (Claude) 팔레트 계획 → (Gemini) 그림 → out/ 에 저장하고 확인용 HTML 만들기

실행 (MemoryNcoLour-main 에서):
    python scripts/try_paint.py "선선한 저녁 노을 아래 한강에서 친구들과 맥주를 마시던 순간"
    python scripts/try_paint.py "…" --no-refs                  # 레퍼런스 없이 (비교용)
    python scripts/try_paint.py "…" --model gemini-3-pro-image  # 다른 모델 (비교용)
    python scripts/try_paint.py "…" --plan-only                # 팔레트 계획까지만 (Gemini 호출 없음)
    python scripts/try_paint.py --dry-run                      # API 없이 가짜 계획·가짜 그림으로 화면만 확인
    python scripts/try_paint.py "…" --no-avoid                 # 직전 그림을 첨부하지 않음 (비교용)

'직전 그림'(기본으로 첨부해서 '이것과 다르게' 지시):
  같은 문장의 예전 그림이 있으면 그중 가장 최근 것, 없으면 out/ 에서 가장 최근에 그린 그림 (어떤 문장이든).
구도 힌트: 같은 문장의 예전 그림들과 직전 그림에 쓴 힌트는 피한다 (--same-layout 이면 끔).

결과: out/paint_<시각>_<문장키 8자>/  painting.png · plan.json · request.txt · info.json · index.html
      + chips.json · card.html (그림에서 뽑은 칩 4개로 만든 결과 카드 = 사이트 결과 화면 시안)
팔레트 계획은 out/plan_cache.json 에 저장 → 같은 문장으로 그림만 다시 그려 볼 때 Claude 를 또 부르지 않는다.

index.html 의 '색 준수율'은 미리보기용 간이 측정이다:
  그림의 각 픽셀을 계획한 색 중 가장 가까운 색에 배정해, 계획 면적 vs 실제 면적과
  '계획한 색에서 ΔE 15 이내인 픽셀 비율'을 보여 준다. (칩 4개 추출은 다음 단계 measure.py)
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompts import color_prompt_v2 as v2                      # noqa: E402
from prompts.paint_prompt import build_paint_request, describe_hex  # noqa: E402

OUT = ROOT / "out"
PLAN_CACHE = OUT / "plan_cache.json"
NEAR_DE = 15.0          # 이 거리 안이면 '계획한 색을 지켰다'로 본다
ASSIGN_MAX_DE = 25.0    # 이보다 멀면 어느 계획 색에도 넣지 않고 '그 밖의 색'


def sentence_key(s: str) -> str:
    """팀 services/mac_memory.sentence_key 와 같은 규칙 (NFKC · 공백 정리 · 소문자 → SHA-256)."""
    try:
        from services.mac_memory import sentence_key as sk
        return sk(s)
    except Exception:
        import re
        import unicodedata
        norm = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip().casefold()
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 색 계산 (numpy)
def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(float) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def hex_rgb(hx: str) -> np.ndarray:
    return np.array([int(hx[i:i + 2], 16) for i in (1, 3, 5)], float)


def fidelity(img: Image.Image, palette: list[dict]) -> dict:
    small = img.convert("RGB").copy()
    small.thumbnail((200, 200))
    lab = rgb_to_lab(np.asarray(small).reshape(-1, 3))
    plan_lab = rgb_to_lab(np.stack([hex_rgb(c["hex"]) for c in palette]))
    d = np.sqrt(((lab[:, None, :] - plan_lab[None, :, :]) ** 2).sum(-1))
    nearest, dist = d.argmin(1), d.min(1)
    n = len(lab)
    rows = []
    for j, c in enumerate(palette):
        got = float(((nearest == j) & (dist <= ASSIGN_MAX_DE)).sum() / n)
        rows.append({"hex": c["hex"], "role": c["role"], "planned": c["share"], "actual": round(got, 3)})
    return {"within_near": round(float((dist <= NEAR_DE).mean()), 3),
            "other": round(float((dist > ASSIGN_MAX_DE).mean()), 3), "rows": rows}


# ---------------------------------------------------------------- 가짜 (dry-run)
def fake_painting(palette, size=(820, 1024), seed=0) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", size, palette[0]["hex"])
    px = np.array(img, dtype=float)
    yy, xx = np.mgrid[0:size[1], 0:size[0]]
    for c in sorted(palette, key=lambda c: -c["share"]):
        for _ in range(max(1, int(c["share"] * 40))):
            cx, cy, r = rnd.uniform(0, size[0]), rnd.uniform(0, size[1]), rnd.uniform(40, 220) * (c["share"] ** 0.5) * 2
            m = ((xx - cx) ** 2 / (r * rnd.uniform(.6, 1.6)) ** 2 + (yy - cy) ** 2 / r ** 2) < 1
            px[m] = hex_rgb(c["hex"])
    return Image.fromarray(px.astype(np.uint8)).filter(ImageFilter.GaussianBlur(10))


# ---------------------------------------------------------------- 화면
def render(folder: Path, sentence, plan, req, info, fid) -> None:
    pal = sorted(plan["palette"], key=lambda c: -c["share"])
    bar = "".join(f"<span style='flex:{c['share']};background:{c['hex']}' title='{c['hex']}'></span>" for c in pal)
    rows = ""
    rmap = {r["hex"]: r for r in fid["rows"]} if fid else {}
    for c in pal:
        r = rmap.get(c["hex"], {"planned": c["share"], "actual": 0.0})
        diff = r["actual"] - r["planned"]
        rows += (f"<tr><td><i style='background:{c['hex']}'></i><b>{html.escape(c['color_name'])}</b> "
                 f"<code>{c['hex']}</code><br><small>{c['role']} · {describe_hex(c['hex'])} · {html.escape(c['reason'])}</small></td>"
                 f"<td>{round(r['planned']*100)}%</td><td>{round(r['actual']*100) if fid else '-'}%</td>"
                 f"<td class='{'up' if diff > 0.05 else 'down' if diff < -0.05 else ''}'>{f'{diff*100:+.0f}' if fid else '-'}</td></tr>")
    p = plan["painting"]
    img_html = ("<img src='painting.png' alt='painting'>" if (folder / "painting.png").exists()
                else "<div class='noimg'>그림 없음 (--plan-only)</div>")
    fid_html = (f"<p class='kpi'>계획한 색에서 ΔE {NEAR_DE:.0f} 이내 픽셀 <b>{round(fid['within_near']*100)}%</b> · "
                f"어느 계획 색과도 먼 픽셀 <b>{round(fid['other']*100)}%</b></p>") if fid else ""
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MaC 그림 시험</title><style>
:root{{--bg:#f7f5f2;--fg:#2b2725;--mute:#8c827a;--line:#e6e1da;--card:#fff}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px;display:grid;grid-template-columns:minmax(0,440px) minmax(0,1fr);gap:24px}}
@media(max-width:820px){{main{{grid-template-columns:1fr}}}}
img{{width:100%;border-radius:8px;box-shadow:0 2px 16px #0002;display:block}}.noimg{{padding:80px 0;text-align:center;border:1px dashed var(--line);color:var(--mute)}}
h1{{font-size:18px;margin:0 0 4px}}.sum{{font-weight:600;font-size:16px;margin:8px 0}}.meta{{color:var(--mute);font-size:12px}}
.bar{{display:flex;height:26px;border-radius:5px;overflow:hidden;margin:10px 0}}.bar span{{display:block}}
table{{border-collapse:collapse;width:100%;background:var(--card)}}th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
th{{font-size:12px;color:var(--mute);font-weight:600}}td i{{display:inline-block;width:14px;height:14px;border-radius:3px;margin-right:6px;vertical-align:-2px;border:1px solid #0001}}
small{{color:var(--mute)}}.up{{color:#a0662b}}.down{{color:#2b5ea0}}.kpi{{margin:12px 0}}
details{{margin-top:16px}}pre{{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);padding:10px;font-size:12px}}
</style></head><body><main><div>{img_html}</div><div>
<h1>{html.escape(sentence)}</h1><div class="sum">{html.escape(plan['memory_summary'])}</div>
<div class="meta">{plan['dominant_emotion']} · {plan['texture']} · {p['medium']} · {p['energy']} · {html.escape(', '.join(p['mood_keywords']))}<br>
팔레트 폭: 명도 {plan.get('spread', {}).get('L_range', '-')} · 채도 {plan.get('spread', {}).get('C_range', '-')} · 색상 계열 {plan.get('spread', {}).get('hue_families', '-')}개<br>
{html.escape(json.dumps(info, ensure_ascii=False))}</div>
<div class="bar">{bar}</div>{fid_html}
<table><tr><th>계획한 색</th><th>계획</th><th>실제</th><th>차이</th></tr>{rows}</table>
<details><summary>그림 모델에 보낸 요청</summary><pre>{html.escape(req['request'])}</pre></details>
<details><summary>고정 스타일 (system)</summary><pre>{html.escape(req['system'])}</pre></details>
</div></main></body></html>"""
    (folder / "index.html").write_text(page, encoding="utf-8")


# ---------------------------------------------------------------- 실행
def get_plan(sentence: str, dry: bool, fresh: bool) -> dict:
    if dry:
        sys.path.insert(0, str(ROOT / "tests"))
        from test_prompts_v2 import GOOD
        return GOOD
    cache = json.loads(PLAN_CACHE.read_text(encoding="utf-8")) if PLAN_CACHE.exists() else {}
    key = f"{v2.PROMPT_VERSION}|{sentence}"
    if key in cache and not fresh:
        print("팔레트 계획: 저장된 응답 사용")
        return cache[key]
    from services.claude_client import claude_json, label
    print(f"팔레트 계획: {label()} 호출 중…")
    raw = claude_json(v2.build_user_prompt(sentence), system=v2.build_system_prompt(), schema=v2.RESPONSE_SCHEMA)
    cache[key] = raw
    PLAN_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sentence", nargs="?")
    ap.add_argument("--no-refs", action="store_true")
    ap.add_argument("--model")
    ap.add_argument("--aspect", default="4:5")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--fresh-plan", action="store_true", help="저장된 팔레트 계획을 무시하고 Claude 다시 호출")
    ap.add_argument("--thinking", choices=["minimal", "medium", "high"], help="그림 모델 생각 정도 (기본: 모델 기본값)")
    ap.add_argument("--timeout", type=float, default=120, help="그림 요청 1번의 최대 대기 초 (기본 120)")
    ap.add_argument("--retries", type=int, default=3, help="503·429 일 때 다시 보낼 횟수 (기본 3)")
    ap.add_argument("--no-fallback", action="store_true", help="첫 모델이 혼잡해도 다른 모델로 넘어가지 않음")
    ap.add_argument("--no-avoid", action="store_true", help="직전 그림을 첨부하지 않음 (기본은 첨부)")
    ap.add_argument("--same-layout", action="store_true", help="예전 그림의 구도 힌트를 피하지 않음")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sentence = a.sentence or ("선선한 저녁 노을 아래 한강에서 친구들과 맥주를 마시며 웃던 여유로운 순간" if a.dry_run else None)
    if not sentence:
        ap.error('문장을 넣어 주세요. 예: python scripts/try_paint.py "기억 문장"')

    OUT.mkdir(exist_ok=True)
    raw = get_plan(sentence, a.dry_run, a.fresh_plan)
    ok, errors, plan = v2.validate_and_normalize(raw)
    if not ok:
        sys.exit(f"팔레트 계획 형식 오류: {errors}")
    if plan["fixes"]:
        print("자동 수정:", "; ".join(plan["fixes"]))

    skey = sentence_key(sentence)
    # 예전 결과 읽기: 같은 문장의 그림들 + 전체에서 가장 최근 그림
    def composition_of(p):
        try:
            c = json.loads((p / "info.json").read_text(encoding="utf-8")).get("composition")
            if c:
                return c
        except Exception:
            pass
        if (p / "request.txt").exists():                     # 예전 결과: request.txt 의 COMPOSITION 줄에서
            for line in (p / "request.txt").read_text(encoding="utf-8").splitlines():
                if line.startswith("COMPOSITION:"):
                    return line.split(":", 1)[1].strip()
        return None

    def made_at(p):
        return (p / "painting.png").stat().st_mtime

    every = [p for p in OUT.glob("paint_*") if (p / "painting.png").exists()]
    same = sorted((p for p in every if p.name.endswith("_" + skey[:8])), key=made_at)
    latest = max(every, key=made_at) if every else None
    prev = same[-1] if same else latest                      # 첨부할 '직전 그림'
    used = [c for c in map(composition_of, same + ([latest] if latest else [])) if c]
    req = build_paint_request(plan, skey, a.aspect, None if a.same_layout else used)
    last_png = prev / "painting.png" if prev else None
    if prev:
        kind = "같은 문장의 직전 그림" if same else "가장 최근 그림(다른 문장)"
        print(f"{kind}: {prev.name} → 이번 구도: {req['composition']}")

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    folder, n = OUT / f"paint_{stamp}_{skey[:8]}", 2
    while folder.exists():                                  # 같은 초에 두 번 실행해도 덮어쓰지 않게
        folder, n = OUT / f"paint_{stamp}{chr(96 + n)}_{skey[:8]}", n + 1
    folder.mkdir()
    (folder / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "request.txt").write_text(req["system"] + "\n\n---\n\n" + req["request"], encoding="utf-8")

    info, fid ={"sentence_key": skey[:12], "prompt": v2.PROMPT_VERSION, "composition": req["composition"]}, None
    if not a.plan_only:
        if a.dry_run:
            img = fake_painting(plan["palette"])
            info.update(model="dry-run", refs=0)
        else:
            from services.painter import load_references, paint
            refs = [] if a.no_refs else load_references()
            if not a.no_refs and not refs:
                print("경고: references/ 에 ref_*.jpg 가 없습니다 → python scripts/prepare_references.py 먼저 실행 (레퍼런스 없이 진행)")
            print(f"그림 생성 중… (레퍼런스 {len(refs)}장, 요청당 최대 {a.timeout:.0f}초, 보통 15~30초)", flush=True)
            avoid = None
            if not a.no_avoid and last_png:
                im = Image.open(last_png).convert("RGB")
                im.thumbnail((512, 512))
                import io as _io
                buf = _io.BytesIO()
                im.save(buf, "JPEG", quality=85)
                avoid = buf.getvalue()
                print(f"직전 그림 첨부: {last_png.parent.name}")
            info["avoid_last"] = bool(avoid)
            img, pinfo = paint(req, refs, model=a.model, aspect=a.aspect, thinking=a.thinking, avoid=avoid,
                               timeout=a.timeout, retries=a.retries, log=lambda m: print(m, flush=True),
                               fallbacks=[] if a.no_fallback else None)
            info.update(pinfo)
        img.save(folder / "painting.png")
        fid = fidelity(img, plan["palette"])
        info["within_near"] = fid["within_near"]
    (folder / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    render(folder, sentence, plan, req, info, fid)
    print(json.dumps(info, ensure_ascii=False))
    print(f"확인용: {folder / 'index.html'}")
    if not a.plan_only:
        from services.card import render_card
        from services.measure import extract_chips
        res = extract_chips(img, plan["palette"], seed=int(skey[:8], 16))
        (folder / "chips.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        (folder / "card.html").write_text(render_card(plan["memory_summary"], res["chips"], res["hash"],
                                                      "painting.png", sentence), encoding="utf-8")
        print("칩: " + " · ".join(f"{c['hex']} {round(c['ratio'] * 100)}%"
                                   f"{' ' + c['color_name'] if c['color_name'] else ''}" for c in res["chips"]))
        print(f"결과 카드: {folder / 'card.html'}")


if __name__ == "__main__":
    main()
