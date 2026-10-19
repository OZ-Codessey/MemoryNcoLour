"""
MaC 장기 기억 저장소 (Supabase PostgREST)
- 'Synthesize Palette' 결과를 public.mac_results 에 저장하고, 챗봇이 검색해 쓴다.
- 서버 전용: SUPABASE_SERVICE_ROLE_KEY 는 절대 브라우저로 내보내지 않는다.
- 모든 함수는 실패해도 예외를 밖으로 던지지 않는다 (저장 실패가 사용자 응답을 막지 않도록).
- 추가 의존성 없음 (requests 만 사용).
"""

import hashlib
import os
import re
import unicodedata

import requests

TABLE = "mac_results"
TIMEOUT = 5
HEX_RE = re.compile(r"^#[0-9A-F]{6}$")

_SELECT_FOR_CHAT = (
    "sentence,memory_summary,chip_names,"
    "chip1_hex,chip2_hex,chip3_hex,chip4_hex,specimen_code,hits,created_at"
)


# ------------------------------------------------------------------------------
# 내부 유틸
# ------------------------------------------------------------------------------
def _cfg():
    url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    return (url, key) if url and key else (None, None)


def is_configured() -> bool:
    return _cfg()[0] is not None


def _headers(extra=None):
    _, key = _cfg()
    h = {"apikey": key, "Content-Type": "application/json"}
    # 예전 service_role 키(JWT, 'eyJ…')만 Authorization 에도 넣는다.
    # 새 secret 키('sb_secret_…')는 JWT 가 아니라 apikey 헤더에만 넣어야 한다 (Supabase 안내).
    if key and key.startswith("eyJ"):
        h["Authorization"] = f"Bearer {key}"
    if extra:
        h.update(extra)
    return h


def sentence_key(sentence: str) -> str:
    """문장 정규화(NFKC·공백 정리·소문자) 후 SHA-256 hex 64자 → sentence_key"""
    norm = unicodedata.normalize("NFKC", sentence or "")
    norm = re.sub(r"\s+", " ", norm).strip().casefold()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------
# 저장 / 캐시 조회
# ------------------------------------------------------------------------------
def lookup_cached(sentence: str):
    """같은 문장이 이미 저장돼 있으면 payload(dict)를 반환하고 조회수를 올린다. 없으면 None."""
    url, _ = _cfg()
    if not url:
        return None
    key = sentence_key(sentence)
    try:
        r = requests.get(
            f"{url}/rest/v1/{TABLE}",
            headers=_headers(),
            params={"sentence_key": f"eq.{key}", "select": "payload", "limit": 1},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        payload = rows[0].get("payload")
        if not isinstance(payload, dict):
            return None
    except Exception as err:
        print(f"[MaC memory] cache lookup failed: {err}")
        return None

    try:  # 조회수 +1 (실패해도 무시)
        requests.post(
            f"{url}/rest/v1/rpc/mac_result_hit",
            headers=_headers(),
            json={"p_key": key},
            timeout=TIMEOUT,
        )
    except Exception as err:
        print(f"[MaC memory] hit update failed: {err}")
    return payload


def save_result(sentence, result_json, palette, memory_summary, specimen_hash, pipeline_version=None) -> bool:
    """분석 결과 저장. 같은 문장이면 무시(ON CONFLICT DO NOTHING). 성공 여부 반환."""
    url, _ = _cfg()
    if not url:
        return False

    try:
        hexes, names = [], []
        for chip in palette[:4]:
            hx = str(chip.get("hex", "")).strip().upper()
            if not HEX_RE.match(hx):
                print("[MaC memory] skip save: invalid hex in palette")
                return False
            hexes.append(hx)
            names.append(str(chip.get("color_name", "")))
        if len(hexes) != 4:
            return False

        row = {
            "sentence_key": sentence_key(sentence),
            "sentence": sentence.strip(),
            "memory_summary": memory_summary,
            "chip1_hex": hexes[0],
            "chip2_hex": hexes[1],
            "chip3_hex": hexes[2],
            "chip4_hex": hexes[3],
            "chip_names": names,
            "specimen_hash": specimen_hash,
            "specimen_code": specimen_hash,
            "payload": result_json,
            "pipeline_version": result_json.get("pipeline_version") or pipeline_version,
            "versions": result_json.get("versions"),
            "image_path": None,
        }
        r = requests.post(
            f"{url}/rest/v1/{TABLE}",
            headers=_headers({"Prefer": "resolution=ignore-duplicates,return=minimal"}),
            params={"on_conflict": "sentence_key"},
            json=row,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return True
    except Exception as err:
        print(f"[MaC memory] save failed: {err}")
        return False


# ------------------------------------------------------------------------------
# 챗봇용 기억 검색
# ------------------------------------------------------------------------------
_STOPWORDS = {
    "기억", "추억", "색깔", "알려줘", "알려", "뭐야", "무엇", "어떤", "있어", "있나요",
    "대해", "관련", "그리고", "에서", "으로", "해줘", "보여줘", "찾아줘", "내가", "제가",
    "저장", "메모리", "에대해", "무슨", "어떻게",
}


def extract_terms(question: str, max_terms: int = 8):
    """질문에서 검색어 추출. 한국어 조사 대응을 위해 어절 끝 1~2글자를 줄인 어근도 함께 쓴다.
    (PostgREST or() 문법을 깨는 특수문자를 막기 위해 한글·영숫자만 허용)"""
    terms = []
    for tok in re.findall(r"[0-9A-Za-z가-힣]{2,}", question or ""):
        if tok in _STOPWORDS:
            continue
        cands = [tok]
        if len(tok) >= 3:
            cands.append(tok[:-1])
        if len(tok) >= 4:
            cands.append(tok[:-2])
        for c in cands:
            if len(c) >= 2 and c not in _STOPWORDS and c not in terms:
                terms.append(c)
    return terms[:max_terms]


def search_memories(question: str, limit: int = 8, recent: int = 4):
    """질문과 관련된 기억(키워드 매칭) + 최근 기억을 합쳐 반환."""
    url, _ = _cfg()
    if not url:
        return []

    def fetch(params):
        r = requests.get(
            f"{url}/rest/v1/{TABLE}",
            headers=_headers(),
            params={"select": _SELECT_FOR_CHAT, **params},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    rows, seen = [], set()

    def add(items):
        for it in items:
            k = (it.get("sentence"), it.get("specimen_code"))
            if k not in seen:
                seen.add(k)
                rows.append(it)

    try:
        terms = extract_terms(question)
        if terms:
            conds = []
            for t in terms:
                conds.append(f"sentence.ilike.*{t}*")
                conds.append(f"memory_summary.ilike.*{t}*")
            add(fetch({"or": f"({','.join(conds)})", "order": "last_hit_at.desc", "limit": limit}))
        if recent > 0:
            add(fetch({"order": "created_at.desc", "limit": recent}))
    except Exception as err:
        print(f"[MaC memory] search failed: {err}")
    return rows


def format_memories(rows) -> str:
    """검색 결과를 LLM 프롬프트에 넣을 텍스트로 변환."""
    if not rows:
        return "(저장된 기억이 없습니다)"
    lines = []
    for i, r in enumerate(rows, 1):
        hexes = [r.get(f"chip{n}_hex") for n in range(1, 5)]
        names = r.get("chip_names") or []
        chips = ", ".join(
            f"{names[n] if n < len(names) and names[n] else '색'}({hexes[n]})"
            for n in range(4) if hexes[n]
        )
        sentence = (r.get("sentence") or "")[:200]
        summary = (r.get("memory_summary") or "")[:200]
        lines.append(
            f"[{i}] 문장: {sentence} | 요약: {summary} | 4색: {chips} | "
            f"코드: {r.get('specimen_code') or '-'} | 조회 {r.get('hits', 1)}회"
        )
    return "\n".join(lines)
