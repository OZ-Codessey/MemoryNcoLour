"""try_sentence.py — 실제 Claude(나누기·고르기)·Voyage 로 기억 문장을 넣어 단계별로 확인한다 (각자 PC, 자기 키).

준비  .env 에 ANTHROPIC_BASE_URL=https://copa.codyssey.kr · ANTHROPIC_API_KEY=sk-cody-... · VOYAGE_API_KEY=... (키는 화면에 출력하지 않음)
      pip install -r requirements.txt
실행  python scripts/try_sentence.py "비 오는 날 할머니 집 마루에서 노란 우산을 쓰고 귤을 까먹던 따뜻한 오후"
      python scripts/try_sentence.py --file 문장들.txt        # 한 줄에 한 문장
      python scripts/try_sentence.py --repeat 3 "문장"        # 같은 문장을 저장소 없이 3번 → 칩이 같은지
      python scripts/try_sentence.py --fresh "문장"           # 예전에 넣은 문장도 저장된 결과 없이 다시 (모델 바꾼 뒤)
결과  화면 출력 + data/kscorpus/try_results.json (sentence_key → 결과. 원문 문장은 저장하지 않음)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.mac_color import JsonFileStore, MemoryStore, NoColors, SplitFailed, analyze_sentence  # noqa: E402
from services.mac_color.clients import load_env_file  # noqa: E402

load_env_file(ROOT / ".env")          # python-dotenv 없이도 .env 를 읽는다

METHOD_KO = {"explicit": "명시 색", "exact": "정확 일치", "embedding": "임베딩", "llm_pick": "후보 선택(RAG)",
             "no_color": "색 없음", "dropped": "버림"}


def show(sentence: str, r: dict) -> None:
    print("=" * 70)
    print(f"문장  {sentence}")
    print(f"모델  나누기 {r['version'].get('splitter')} · 고르기 {r['version'].get('picker')}")
    print(f"요약  {r['memory_summary']}   질감 {r['texture']}   코드 {r['specimen_code']}{'  (저장된 결과)' if r.get('cached') else ''}")
    print("단어")
    for i in r["items"]:
        sc = f" 점수 {i['score']:.3f}" if i.get("score") is not None else ""
        cw = f" [색 표현 '{i['color_word']}']" if i.get("color_word") else ""
        print(f"  {i['axis']:12s} {i['text']}{cw} → {METHOD_KO.get(i['method'], i['method'])}{sc} → {i['color'] or '-'}"
              f"{'  · ' + i['note'] if i.get('note') else ''}")
    print("칩 4개")
    for c in r["palette"]:
        print(f"  {c['role']:3s} {c['name']:10s} {c['hex']}  {int(c['weight'] * 100)}%  {c['reason']}")
    print("그림 팔레트")
    for p in r["paint_palette"]:
        print(f"  {p['name']:10s} {p['hex']}  {p['area'] * 100:5.1f}%{'  (파생)' if p['derived'] else ''}")
    if r.get("pending"):
        print(f"검토 대상(pending) {len(r['pending'])}개: " + ", ".join(f"{p['text']}({p['method']})" for p in r["pending"]))
    if r.get("pick_error"):
        print(f"고르기 단계 오류: {r['pick_error']}")


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("sentences", nargs="*")
    ap.add_argument("--file")
    ap.add_argument("--repeat", type=int, default=1, help="저장소 없이 같은 문장을 여러 번 → 칩이 같은지 확인")
    ap.add_argument("--prompt", action="store_true", help="그림 지시문도 출력")
    ap.add_argument("--fresh", action="store_true", help="저장된 결과를 쓰지 않고 다시 분석 (모델을 바꾼 뒤 확인할 때)")
    args = ap.parse_args()
    sents = list(args.sentences)
    if args.file:
        sents += [ln.strip() for ln in Path(args.file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not sents:
        sys.exit("문장을 넣어 주세요. 예: python scripts/try_sentence.py \"문장\"")
    store = JsonFileStore(ROOT / "data" / "kscorpus" / "try_results.json")
    for s in sents:
        try:
            if args.repeat > 1:
                codes = []
                for n in range(args.repeat):
                    r = analyze_sentence(s, store=MemoryStore())
                    codes.append((r["specimen_code"], tuple(c["name"] for c in r["palette"])))
                    print(f"  {n + 1}회: {r['specimen_code']} {' · '.join(c['name'] for c in r['palette'])}")
                print("  → 모두 같음" if len(set(codes)) == 1 else "  → 달라짐 (나누기 결과가 흔들림 — 저장소가 같은 문장을 고정해 줌)")
                continue
            r = analyze_sentence(s, store=store, use_cache=not args.fresh)
            show(s, r)
            if args.prompt:
                print("그림 지시문\n" + r["paint_prompt"])
        except NoColors as e:
            print(f"색을 정하지 못함 → 다시 입력 요청: {e}")
        except SplitFailed as e:
            print(f"나누기 실패: {e}")


if __name__ == "__main__":
    main()
