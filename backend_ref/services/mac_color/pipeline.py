"""pipeline.py — 기억 문장 하나 → 칩 4개 + 그림 팔레트 + 그림 지시문.

  ① 저장소에 같은 문장(sentence_key)이 있으면 그 결과를 그대로 돌려준다      (같은 문장 = 같은 칩)
  ② Claude: 문장 → 7축 단어 + 기억 질감 + 요약                               (prompts.build_split_prompt)
  ③ 매칭: 명시 색 → 정확 일치 → 임베딩(τ) → Claude 후보 고르기(RAG)            (matcher)
     나누기·고르기 모델은 환경변수 MAC_SPLIT_LLM · MAC_PICK_LLM 로 바꾼다 (둘 다 기본 claude)
  ④ 면적·파생색·칩                                                            (palette)
  ⑤ 표본 코드(specimen_hash) + 그림 지시문                                     (keys, prompts.build_paint_prompt)
  ⑥ 저장소에 저장 (원문 문장은 저장하지 않음 — 열쇠와 결과만)

호출은 문장당 Claude 1번(나누기) + 고를 것이 있을 때 Claude 1번, Voyage 최대 1번.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from . import keys, palette, prompts
from .data import AXES, Corpus, load
from .matcher import apply_picks, match_items

PIPELINE_VERSION = "mac-ks-1"
TEXTURES = {"소중한", "아련한", "생생한", "없음"}


class SplitFailed(Exception):
    pass


# ---------------------------------------------------------------- 결과 저장소
class MemoryStore:
    """프로세스 메모리 (Vercel 에서는 인스턴스가 살아 있는 동안만). 팀이 저장소를 정하면 같은 get/put 으로 바꿔 끼운다."""

    def __init__(self):
        self.d = {}

    def get(self, k):
        return copy.deepcopy(self.d.get(k))

    def put(self, k, v):
        self.d[k] = copy.deepcopy(v)


class JsonFileStore(MemoryStore):
    """로컬 시험용: 파일 하나에 sentence_key → 결과."""

    def __init__(self, path):
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self.d = json.loads(self.path.read_text(encoding="utf-8"))

    def put(self, k, v):
        super().put(k, v)
        self.path.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding="utf-8")


def _default_store():
    from .store_supabase import store_from_env
    return store_from_env() or MemoryStore()

_DEFAULT_STORE = _default_store()
# ---------------------------------------------------------------- 단계
def split(sentence: str, corpus: Corpus, llm) -> dict:
    prompt = prompts.build_split_prompt(sentence, corpus)
    last = None
    for _ in range(2):                                   # 답의 형식이 틀렸을 때만 한 번 더
        try:
            r = llm(prompt)
        except Exception as e:                           # API 실패(503·429)는 다시 부르지 않는다 — 클라이언트 안에서 이미 재시도함.
            raise SplitFailed(f"나누기 호출 실패: {str(e)[:200]}") from e   # 여기서 또 부르면 요청 한도를 바로 넘긴다
        try:
            if isinstance(r, str):
                r = json.loads(r)
            items = [it for it in r.get("items", []) if isinstance(it, dict) and it.get("text")]
            tex = r.get("texture", "없음")
            return {"summary": str(r.get("summary", "")).strip(),
                    "texture": tex if tex in TEXTURES else "없음",
                    "items": [{"axis": str(it.get("axis", "")).strip(), "text": str(it["text"]).strip(),
                               "color_word": str(it.get("color_word", "") or "").strip()} for it in items][:12]}
        except Exception as e:
            last = e
    raise SplitFailed(f"나누기 답 형식 오류: {last}")


def default_picker() -> str:
    """고르기 모델: MAC_PICK_LLM 이 있으면 그것, 없으면 claude (나누기와 같은 Codyssey 키 하나로 동작)."""
    from .clients import load_env_file
    load_env_file()
    return os.getenv("MAC_PICK_LLM") or "claude"


def analyze_sentence(sentence: str, *, llm=None, splitter=None, embedder=None, store=None,
                     corpus: Corpus | None = None, use_cache: bool = True) -> dict:
    """llm      : 후보 고르기 함수 (기본: MAC_PICK_LLM, claude)
       splitter : 문장 나누기 함수 (기본: MAC_SPLIT_LLM, claude). 시험에서 llm 만 넘기면 나누기도 그 함수로 한다."""
    corpus = corpus or load()
    store = store if store is not None else _DEFAULT_STORE
    skey = keys.sentence_key(sentence)
    if use_cache:
        hit = store.get(skey)
        if hit:
            hit["cached"] = True
            return hit

    if splitter is None and llm is not None:                # 시험용: 가짜 함수 하나로 나누기·고르기 모두
        splitter = llm
    if splitter is None:
        from .clients import make_llm
        splitter = make_llm(os.getenv("MAC_SPLIT_LLM", "claude"), prompts.SPLIT_SCHEMA)
    if llm is None:
        from .clients import make_llm
        llm = make_llm(default_picker(), prompts.PICK_SCHEMA)
    if embedder is None and corpus.vectors is not None:
        from .clients import VoyageEmbedder
        embedder = VoyageEmbedder(corpus.settings)

    sp = split(sentence, corpus, splitter)
    matches, tasks = match_items(sp["items"], corpus, embedder)
    pick_error = None
    if tasks:
        try:
            ans = llm(prompts.build_pick_prompt(sentence, tasks))
            if isinstance(ans, str):
                ans = json.loads(ans)
            apply_picks(matches, tasks, ans.get("answers"), corpus)
        except Exception as e:                            # 고르기 실패: 맥락 색은 첫 번째, 단어 후보는 버림
            pick_error = f"{type(e).__name__}: {str(e)[:160]}"     # 원인을 같이 남긴다 (키·문장은 들어가지 않음)
            apply_picks(matches, tasks, None, corpus)

    pal = palette.build_palette(matches, sp["texture"], corpus)     # NoColors 는 호출한 쪽에서 '다시 입력' 처리
    shash = keys.specimen_hash(skey, pal["chips"])
    result = {
        "version": {"pipeline": PIPELINE_VERSION, **corpus.versions,
                    "splitter": getattr(splitter, "label", "custom"), "picker": getattr(llm, "label", "custom")},
        "sentence_key": skey,
        "sentence": sentence,
        "specimen_hash": shash,
        "specimen_code": keys.specimen_code(shash),
        "memory_summary": sp["summary"],
        "texture": pal["texture"],
        "palette": pal["chips"],                        # 칩 4개 (화면·메일)
        "paint_palette": pal["paint_palette"],          # 그림에 쓰는 전체 색과 면적
        "paint_prompt": prompts.build_paint_prompt(sp["summary"], pal["paint_palette"], pal["texture"]),
        "items": [{"axis": m.axis, "text": m.text, "color_word": m.color_word, "method": m.method,
                   "word_id": m.word_id, "color": m.color, "score": None if m.score is None else round(m.score, 4),
                   "note": m.note} for m in matches],
        "pending": [{"axis": m.axis, "text": m.text, "method": m.method, "color": m.color}
                    for m in matches if m.method in ("llm_pick", "dropped")],
        "pick_error": pick_error,
        "cached": False,
    }
    store.put(skey, result)
    return result
