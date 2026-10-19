"""KS A 0011 한국어 색이름 시험 (오프라인). 실행: python tests/test_ks_names.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.ks_names import _data, apply_ks_names, has_batchim, ks_color_name  # noqa: E402
from services.ks_systematic import normalize  # noqa: E402


def test_idiom_exact():
    d = _data()[0]
    for name, hx, *_ in d["idioms"]:
        got = ks_color_name(hx)
        assert got["kind"] == "관용", (hx, got)
        # 같은 HEX 를 가진 관용색이 여럿이면(예: 루비색·크림슨) 그중 하나
        same = {n for n, h, *_ in d["idioms"] if h == hx}
        assert got["name"] in same, (hx, got, same)


def test_known_examples():
    assert ks_color_name("#FFB982")["name"] == "살구색"
    assert ks_color_name("#E1D9CB")["name"] == "진주색"          # 진주색 #EBE2D5 와 ΔE00 2.2
    far = ks_color_name("#A8B89A")                              # 회녹색 계열 — KS 관용색과 멂
    assert far["kind"] == "계통" and far["name"].endswith(("녹색", "연두", "초록")), far
    assert ks_color_name("#7B8086")["name"] in ("회색", "파란 회색", "밝은 회색") or ks_color_name("#7B8086")["kind"] == "관용"


def test_systematic_pipeline_on_ks_values():
    """KS 표 8 계통색 대표값(HEX) → 격자 보간 + 규칙 → 공식 계통색이름과 비교 (관용색 판정은 빼고)."""
    import services.ks_names as kn
    ks_path = ROOT / "backend_ref" / "data" / "kscorpus" / "ks_colors.json"
    if not ks_path.exists():
        print("   (backend_ref 없음 — 건너뜀)")
        return
    from services.ks_systematic import systematic_name
    table = _data()[4]
    rows = json.loads(ks_path.read_text(encoding="utf-8"))["colors"]
    hit = sum(normalize(systematic_name(*kn.munsell_of(c["hex"]), table)) == normalize(c.get("systematic") or c["name"])
              for c in rows)
    rate = hit / len(rows)
    print(f"   HEX→계통색이름 일치 {hit}/{len(rows)} ({rate:.0%})")
    assert rate >= 0.6, rate


def test_apply_keeps_english_and_is_deterministic():
    pal = [{"hex": "#E8B98A", "color_name": "Dusty Apricot Glow", "reason": "r", "weight": 0.5},
           {"hex": "#3A414C", "color_name": "Ashen Indigo", "reason": "r", "weight": 0.5}]
    a, b = apply_ks_names(pal), apply_ks_names(pal)
    assert [c["color_name"] for c in a] == [c["color_name"] for c in b]
    assert a[0]["color_name_en"] == "Dusty Apricot Glow" and a[0]["color_name"] != "Dusty Apricot Glow"
    assert all(ord(ch) >= 0xAC00 or ch == " " for ch in "".join(c["color_name"] for c in a)), a
    assert apply_ks_names(a)[0]["color_name_en"] == "Dusty Apricot Glow"      # 두 번 붙여도 영문 유지


def test_josa():
    assert has_batchim("살구색") and has_batchim("하양") and not has_batchim("보라")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
