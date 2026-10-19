"""data.py — KS 색이름표 · 단어 코퍼스 · 임베딩 벡터를 한 번만 읽어 메모리에 둔다.

파일 (저장소 data/kscorpus/)
  ks_colors.json          KS A 0011 색 (colors: 쓰는 색 / substitutes: 값 없는 이름 → 대신 쓸 이름)
  words.json              단어 코퍼스 (7축 + none) + explicit_colors(문장에 직접 나온 색 표현)
  word_vectors.npz/.meta  코퍼스 표기 임베딩 (meta.settings = 런타임 질의도 같은 설정으로)

세 파일의 버전(해시)이 서로 맞지 않으면 시작할 때 바로 오류를 낸다 — 엇갈린 파일로 조용히 틀린 색을 내지 않도록.
"""
from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
KS_DIR = Path(os.getenv("MAC_KS_DIR", ROOT / "data" / "kscorpus"))

AXES = ["emotion", "memory_color", "time", "space", "temperature", "light", "sensory"]


def norm_text(s: str) -> str:
    """비교용 정규화: 유니코드 NFC, 앞뒤 공백 제거, 연속 공백 하나로."""
    return " ".join(unicodedata.normalize("NFC", str(s)).split())


def squash(s: str) -> str:
    """띄어쓰기까지 무시한 비교용 ('흑백 사진' = '흑백사진')."""
    return norm_text(s).replace(" ", "")


@dataclass
class Corpus:
    colors: dict                     # KS 이름 → 색 항목
    substitutes: dict                # 값 없는 KS 이름 → 항목(use_instead …)
    words: dict                      # word_id → 단어 줄
    surface: dict                    # 정규화 표기 → word_id
    surface_sq: dict                 # 띄어쓰기 없앤 표기 → word_id (위에서 못 찾을 때)
    explicit: dict                   # 정규화 색 표현 → KS 이름
    vec_rows: list                   # 벡터 행 i → {text, word_id, axis}
    vectors: np.ndarray | None       # (n, dim) float32, 길이 1
    axis_rows: dict                  # 축 → 그 축 벡터 행 번호 배열
    settings: dict                   # 임베딩 설정 (model, dim, input_type, text_format, axis_label)
    versions: dict = field(default_factory=dict)

    def color(self, name: str) -> dict:
        """KS 이름 → 실제로 쓰는 색 항목 (값 없는 이름은 대신 쓸 이름으로)."""
        if name in self.colors:
            return self.colors[name]
        if name in self.substitutes:
            return self.colors[self.substitutes[name]["use_instead"]]
        raise KeyError(f"KS 색이름표에 없는 이름: {name}")


@lru_cache(maxsize=1)
def load(ks_dir: str | None = None) -> Corpus:
    d = Path(ks_dir) if ks_dir else KS_DIR
    ks = json.loads((d / "ks_colors.json").read_text(encoding="utf-8"))
    wj = json.loads((d / "words.json").read_text(encoding="utf-8"))
    if wj["ks_colors_hash"] != ks["content_hash"]:
        raise RuntimeError(f"words.json 이 다른 ks_colors 로 만들어졌다 ({wj['ks_colors_hash']} ≠ {ks['content_hash']}). build_words.py 를 다시 돌릴 것")

    colors = {c["name"]: c for c in ks["colors"]}
    subs = {s["name"]: s for s in ks["substitutes"]}
    words = {w["id"]: w for w in wj["words"]}
    surface, surface_sq = {}, {}
    for w in wj["words"]:
        for s in [w["word"]] + w["aliases"]:
            surface[norm_text(s)] = w["id"]
            surface_sq.setdefault(squash(s), w["id"])
    explicit = {norm_text(e["text"]): e["ks"] for e in wj["explicit_colors"]}

    vec_rows, vectors, axis_rows, settings = [], None, {}, {}
    meta_p, npz_p = d / "word_vectors.meta.json", d / "word_vectors.npz"
    if meta_p.exists() and npz_p.exists():
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        if meta.get("fake"):
            raise RuntimeError("word_vectors 가 시험용 가짜 벡터다. build_embeddings.py 로 실제 벡터를 만들 것")
        if meta["words_hash"] != wj["content_hash"]:
            raise RuntimeError(f"word_vectors 가 다른 words.json 으로 만들어졌다 ({meta['words_hash']} ≠ {wj['content_hash']}). "
                               "build_embeddings.py 를 다시 돌릴 것")
        vec_rows = meta["rows"]
        vectors = np.load(npz_p)["vectors"].astype(np.float32)
        if vectors.shape[0] != len(vec_rows):
            raise RuntimeError("word_vectors 행 수가 meta 와 다르다")
        for a in AXES + ["none"]:
            axis_rows[a] = np.array([i for i, r in enumerate(vec_rows) if r["axis"] == a], dtype=np.int64)
        settings = meta["settings"]

    return Corpus(colors=colors, substitutes=subs, words=words, surface=surface, surface_sq=surface_sq,
                  explicit=explicit, vec_rows=vec_rows, vectors=vectors, axis_rows=axis_rows, settings=settings,
                  versions={"ks": ks["content_hash"], "words": wj["content_hash"],
                            "vectors": (settings.get("model", "") + "/" + str(settings.get("dim", ""))) if settings else "none"})
