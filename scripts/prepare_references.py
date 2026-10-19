"""
레퍼런스 그림 준비: 원본 → 긴 변 1024px JPEG → references/ref_01.jpg …

실행 (MemoryNcoLour-main 에서):
    python scripts/prepare_references.py                    # 기본: images/ 의 레퍼런스 3장
    python scripts/prepare_references.py a.png b.webp c.jpg # 직접 고른 파일들

- 이미 references/ 에 있던 ref_*.jpg 는 지우고 새로 만든다 (순서 = 인자 순서).
- 투명 배경(RGBA)은 흰색 위에 합친다.
- 그림 요청 때마다 이 폴더의 ref_*.jpg 를 전부 첨부한다 → 바꾸고 싶으면 이 폴더만 바꾸면 된다.
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "references"
DEFAULT = ["images/166174e5f3068e0b.webp", "images/07_02_yellow_navy_orange.webp", "images/Pasted_Graphic_2.webp"]
MAX_SIDE = 1024


def main(paths):
    srcs = [Path(p) if Path(p).is_absolute() else ROOT / p for p in (paths or DEFAULT)]
    missing = [str(p) for p in srcs if not p.exists()]
    if missing:
        sys.exit("파일이 없습니다: " + ", ".join(missing))
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("ref_*.jpg"):
        old.unlink()
    for i, src in enumerate(srcs, 1):
        im = Image.open(src)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        w, h = im.size
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        dst = OUT / f"ref_{i:02d}.jpg"
        im.save(dst, "JPEG", quality=90)
        print(f"{src.name} ({w}x{h}) → {dst.relative_to(ROOT)} ({im.size[0]}x{im.size[1]}, {dst.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    main(sys.argv[1:])
