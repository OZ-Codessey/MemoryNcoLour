"""keys.py — 같은 문장이면 같은 결과를 꺼내기 위한 열쇠.

sentence_key  = SHA-256("mac-sentence-v1|" + 정규화한 문장)
    정규화: 유니코드 NFC, 앞뒤 공백 제거, 연속 공백 하나로. (맞춤법·문장부호는 그대로 — 글자가 다르면 다른 문장)
    코퍼스 버전은 넣지 않는다: 코퍼스가 바뀌어도 이미 저장된 문장은 예전 결과를 그대로 돌려준다(팀 기본값).
specimen_hash = SHA-256(sentence_key + 칩 4개의 "이름|hex" 를 순서대로) → 화면·메일에 보이는 표본 코드 MaC-XXXX-XXXX-XXXX
    같은 문장 + 같은 칩 → 항상 같은 코드. 그림은 생성 모델이라 매번 다를 수 있어 해시에 넣지 않는다.
원문 문장은 저장하지 않는다 — 열쇠(해시)만 저장한다(팀 기본값).
"""
from __future__ import annotations

import hashlib

from .data import norm_text

SENTENCE_SALT = "mac-sentence-v1|"


def sentence_key(sentence: str) -> str:
    return hashlib.sha256((SENTENCE_SALT + norm_text(sentence)).encode("utf-8")).hexdigest()


def specimen_hash(skey: str, chips: list[dict]) -> str:
    body = skey + "".join(f"\n{c['name']}|{c['hex'].upper()}" for c in chips)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def specimen_code(shash: str) -> str:
    h = shash[:12].upper()
    return f"MaC-{h[0:4]}-{h[4:8]}-{h[8:12]}"
