"""matcher.py — 언어 모델(기본 Claude)이 나눈 단어 하나하나를 KS 색으로 잇는다 (규칙 B).

단어마다 이 순서로 찾고, 먼저 걸린 곳에서 멈춘다.
  1. 명시 색   문장에 색이 직접 붙어 있으면('노란 우산') 그 색 — words.json explicit_colors           method=explicit
  2. 정확 일치 코퍼스 표기와 글자가 같으면(띄어쓰기 무시 포함) 그 단어 줄                          method=exact
  3. 임베딩    같은 축 표기들과 비교해 1등 점수 ≥ τ 이고 2등 단어와 차이 ≥ gap 이면 그 단어 줄    method=embedding
  4. 고르기    3에서 못 정하면 (기본 Claude, MAC_PICK_LLM) 상위 K개 후보 단어를 주고 고르게 한다 (후보 밖 답은 버림)           method=llm_pick
  5. 버림      고를 후보가 없거나 '없음'이면 색을 정하지 않는다 → pending 에 남겨 코퍼스 보강 대상으로  method=dropped
  · 단어 줄이 '색 없음(none)'이면 색을 정하지 않는다 (명시 색이 있으면 1에서 이미 정해짐)                  method=no_color
  · 단어 줄의 색이 여럿('맥락')이면 문장 맥락으로 하나를 고른다 — 4와 같은 Gemini 호출 한 번에 묶는다

기준값 (2026-10-18, 최종 벡터로 '한 개 빼고 찾기' 시험, 3,946 표기)
  τ=0.90, gap=0.02 → 약 31% 바로 받아들임, 그중 91% 색 정답 / 상위 20 후보 안에 맞는 색 97%
  실제 문장 평가로 다시 확정한다. 환경변수 MAC_TAU, MAC_GAP, MAC_TOPK 로 바꿀 수 있다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from .data import AXES, Corpus, norm_text, squash

TAU = float(os.getenv("MAC_TAU", "0.90"))
GAP = float(os.getenv("MAC_GAP", "0.02"))
TOPK = int(os.getenv("MAC_TOPK", "20"))


@dataclass
class Match:
    axis: str                        # 최종 축 (정확 일치한 단어 줄이 다른 축이면 그 축으로 고침)
    text: str                        # 나누기 단계가 낸 표기
    color_word: str = ""
    method: str = ""                 # explicit / exact / embedding / llm_pick / no_color / dropped
    word_id: str | None = None
    score: float | None = None       # 임베딩 1등 점수 (있을 때)
    color: str | None = None         # 확정된 KS 이름
    options: list = field(default_factory=list)   # 맥락 단어의 색 후보 또는 Gemini 단어 후보
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.color is not None


# ---------------------------------------------------------------- 1. 명시 색
def find_explicit(color_word: str, corpus: Corpus) -> str | None:
    """'노란', '새빨간색으로', '노랗던' 같은 표현 → KS 이름. 활용형은 '~다' 를 뗀 줄기로 앞부분을 맞춘다."""
    cw = norm_text(color_word)
    if not cw:
        return None
    if cw in corpus.explicit:
        return corpus.explicit[cw]
    best, best_len = None, 0
    for key, ks in corpus.explicit.items():
        stem = key[:-1] if key.endswith("다") else key
        if len(stem) >= 2 and cw.startswith(stem) and len(stem) > best_len:
            best, best_len = ks, len(stem)
    return best


# ---------------------------------------------------------------- 2. 정확 일치
def find_exact(text: str, corpus: Corpus) -> str | None:
    t = norm_text(text)
    return corpus.surface.get(t) or corpus.surface_sq.get(squash(t))


def _apply_row(m: Match, wid: str, corpus: Corpus, method: str) -> None:
    w = corpus.words[wid]
    m.word_id, m.method = wid, method
    if w["axis"] != m.axis:
        m.note = (m.note + f" 축 고침 {m.axis}→{w['axis']}").strip()
        m.axis = w["axis"]
    if m.color:                               # 명시 색이 이미 정해졌으면 그대로
        return
    names = [c["name"] for c in w["colors"]]
    if not names:
        m.method = "no_color"
    elif len(names) == 1:
        m.color = names[0]
    else:
        m.options = names                     # 맥락 단어 → Gemini 가 고름 (실패하면 첫 번째)


# ---------------------------------------------------------------- 3. 임베딩
def rank_words(qvec: np.ndarray, axis: str, corpus: Corpus, k: int) -> list[tuple[str, float]]:
    """같은 축 + 색 없음(none) 표기들과 비교 → 단어 줄 단위로 중복 없이 점수 높은 순."""
    idx = np.concatenate([corpus.axis_rows.get(axis, np.array([], dtype=np.int64)),
                          corpus.axis_rows.get("none", np.array([], dtype=np.int64))])
    if idx.size == 0:
        return []
    sims = corpus.vectors[idx] @ qvec
    out, seen = [], set()
    for j in np.argsort(-sims, kind="stable"):
        wid = corpus.vec_rows[idx[j]]["word_id"]
        if wid in seen:
            continue
        seen.add(wid)
        out.append((wid, float(sims[j])))
        if len(out) >= k:
            break
    return out


def rank_words_lexical(text: str, axis: str, corpus: Corpus, k: int) -> list[tuple[str, float]]:
    """임베딩을 못 쓸 때(키 없음·제한 초과)의 대체 후보: 글자 두 개씩 겹치는 정도."""
    def grams(s):
        s = squash(s)
        return {s[i:i + 2] for i in range(len(s) - 1)} or {s}
    q = grams(text)
    best: dict[str, float] = {}
    for w in corpus.words.values():
        if w["axis"] not in (axis, "none"):
            continue
        for s in [w["word"]] + w["aliases"]:
            g = grams(s)
            sc = len(q & g) / len(q | g)
            if sc > best.get(w["id"], 0):
                best[w["id"]] = sc
    return sorted(best.items(), key=lambda x: (-x[1], x[0]))[:k]


def embed_query_text(text: str, axis: str, corpus: Corpus) -> str:
    st = corpus.settings
    return st["text_format"].format(text=norm_text(text), axis=st["axis_label"][axis])


# ---------------------------------------------------------------- 전체
def match_items(items: list[dict], corpus: Corpus, embedder=None) -> tuple[list[Match], list[dict]]:
    """items: 나누기 결과 [{axis, text, color_word}]
       반환: (Match 목록, 고르기 모델에게 물어볼 tasks)  — tasks 는 pick 단계에서 한 번에 묻는다."""
    matches: list[Match] = []
    need_embed: list[Match] = []
    for it in items:
        axis = str(it.get("axis", "")).strip()
        text = norm_text(it.get("text", ""))
        if not text:
            continue
        m = Match(axis=axis if axis in AXES else "", text=text, color_word=norm_text(it.get("color_word", "") or ""))
        ks = find_explicit(m.color_word, corpus) if m.color_word else None
        if ks:
            m.color, m.method = ks, "explicit"
        wid = find_exact(text, corpus)
        if wid:
            _apply_row(m, wid, corpus, m.method or "exact")
        elif m.color:
            if not m.axis:
                m.axis = "memory_color"       # 색이 직접 붙은 대상인데 축을 모르면 사물로
        elif m.axis:
            need_embed.append(m)
        else:
            m.method, m.note = "dropped", "축을 알 수 없음"
        matches.append(m)

    # 임베딩 (한 번에)
    ranked: dict[int, list[tuple[str, float]]] = {}
    if need_embed:
        vecs = None
        if embedder is not None and corpus.vectors is not None:
            try:
                vecs = embedder([embed_query_text(m.text, m.axis, corpus) for m in need_embed])
            except Exception as e:                       # 키 없음·제한 초과·네트워크 → 글자 비교 후보로
                for m in need_embed:
                    m.note = f"임베딩 실패({type(e).__name__}: {str(e)[:120]}) → 글자 비교 후보"   # 원인까지 (키는 안 들어감)
        for i, m in enumerate(need_embed):
            if vecs is not None:
                r = rank_words(vecs[i], m.axis, corpus, TOPK)
                if r:
                    m.score = r[0][1]
                    gap = r[0][1] - (r[1][1] if len(r) > 1 else 0.0)
                    if r[0][1] >= TAU and gap >= GAP:
                        _apply_row(m, r[0][0], corpus, "embedding")
                        continue
            else:
                r = rank_words_lexical(m.text, m.axis, corpus, TOPK)
            ranked[id(m)] = [(wid, s) for wid, s in r if corpus.words[wid]["axis"] != "none"]

    # Gemini 에게 물을 것
    tasks = []
    for n, m in enumerate(matches):
        if m.method in ("explicit", "exact", "embedding") and m.options and not m.color:
            tasks.append({"id": f"t{n}", "item": m.text, "axis": m.axis, "kind": "color", "options": m.options})
        elif id(m) in ranked:
            cands = ranked[id(m)]
            if cands:
                m.options = [wid for wid, _ in cands]
                tasks.append({"id": f"t{n}", "item": m.text, "axis": m.axis, "kind": "word",
                              "options": [{"id": wid, "word": corpus.words[wid]["word"],
                                           "colors": [c["name"] for c in corpus.words[wid]["colors"]]} for wid, _ in cands]})
            else:
                m.method = "dropped"
    return matches, tasks


def apply_picks(matches: list[Match], tasks: list[dict], answers: list[dict] | None, corpus: Corpus) -> None:
    """Gemini 답을 검증해서 반영한다. 후보 밖 답·형식 오류는 버린다(맥락 색은 첫 번째로)."""
    by_id = {str(a.get("id")): a for a in (answers or []) if isinstance(a, dict)}
    for t in tasks:
        m = matches[int(t["id"][1:])]
        a = by_id.get(t["id"], {})
        if t["kind"] == "color":
            c = a.get("color")
            m.color = c if c in m.options else m.options[0]
            if c not in m.options:
                m.note = (m.note + " 맥락 색 기본값(첫 번째)").strip()
            continue
        wid = a.get("word")
        if wid not in m.options:                       # '없음' 또는 후보 밖
            m.method = "dropped"
            m.note = (m.note + " 고르기: 맞는 후보 없음").strip()
            continue
        _apply_row(m, wid, corpus, "llm_pick")
        names = [c["name"] for c in corpus.words[wid]["colors"]]
        if m.color is None and names:
            c = a.get("color")
            m.color = c if c in names else names[0]
