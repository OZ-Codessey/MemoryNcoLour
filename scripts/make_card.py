"""
이미 그린 그림 폴더로 칩 4개를 뽑고 결과 카드(card.html)를 만든다. API 를 부르지 않는다 (비용 없음).

실행 (MemoryNcoLour-main 에서):
    python scripts/make_card.py                         # out/ 의 가장 최근 그림 폴더
    python scripts/make_card.py out/paint_20261020_…    # 특정 폴더
    python scripts/make_card.py --all                   # out/ 의 모든 그림 폴더

결과: 그 폴더에 chips.json · card.html
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.card import render_card       # noqa: E402
from services.measure import extract_chips  # noqa: E402


def make(folder: Path) -> Path:
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8")) if (folder / "plan.json").exists() else {}
    key = folder.name.rsplit("_", 1)[-1]
    seed = int(key, 16) if all(ch in "0123456789abcdef" for ch in key) else 0
    res = extract_chips(Image.open(folder / "painting.png"), plan.get("palette"), seed=seed)
    (folder / "chips.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    sentence = None
    if (folder / "index.html").exists():                     # 확인용 화면의 <h1> 에서 원문 문장
        t = (folder / "index.html").read_text(encoding="utf-8")
        if "<h1>" in t:
            import html as _h
            sentence = _h.unescape(t.split("<h1>", 1)[1].split("</h1>", 1)[0])
    page = render_card(plan.get("memory_summary", ""), res["chips"], res["hash"], "painting.png", sentence)
    (folder / "card.html").write_text(page, encoding="utf-8")
    return folder / "card.html"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    out = ROOT / "out"
    folders = sorted((p for p in out.glob("paint_*") if (p / "painting.png").exists()),
                     key=lambda p: (p / "painting.png").stat().st_mtime)
    if a.folder:
        folders = [Path(a.folder) if Path(a.folder).is_absolute() else ROOT / a.folder]
    elif not a.all:
        folders = folders[-1:]
    if not folders:
        sys.exit("그림 폴더가 없습니다 (out/paint_*/painting.png)")
    for f in folders:
        page = make(f)
        chips = json.loads((f / "chips.json").read_text(encoding="utf-8"))["chips"]
        desc = " · ".join(f"{c['hex']} {round(c['ratio']*100)}%{' ' + c['color_name'] if c['color_name'] else ''}" for c in chips)
        print(f"{f.name}: {desc}\n  → {page}")


if __name__ == "__main__":
    main()
