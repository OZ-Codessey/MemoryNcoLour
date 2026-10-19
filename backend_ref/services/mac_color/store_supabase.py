"""store_supabase.py — 결과 저장소를 Supabase 테이블 mac_results 로 (같은 문장 = 같은 칩·코드, 서버 여러 대여도).

엔진(pipeline.analyze_sentence)은 저장소에 get(k)·put(k, v) 두 함수만 요구한다. 이 클래스가 그 두 함수를 Supabase 로 구현한다.

  get(k)    : mac_results 에서 sentence_key = k 인 결과를 꺼낸다. 있으면 hits 를 1 올린다.
  put(k, v) : 처음 나온 문장의 결과를 넣는다. 테이블에서 바로 보이게 문장 · 요약 · 칩 4개 hex · 색이름 · 표본 코드를
              열로도 나눠 넣고, 결과 전체는 payload 에 넣는다.
              입력 문장은 결과의 "sentence" 에서 가져온다 → pipeline.py 결과 dict 에 "sentence": sentence 한 줄이 필요하다.
              이미 다른 서버가 먼저 넣었으면(동시에 같은 문장) 먼저 넣은 결과로
              v 를 바꿔 준다 → 두 사람이 동시에 같은 문장을 넣어도 같은 칩이 나간다.
              결과의 pending(코퍼스에서 못 찾았거나 LLM 이 고른 단어)은 mac_pending 에 단어 단위로 센다.

지키는 것
  · 입력 문장은 mac_results 에만 들어간다. 이 테이블은 RLS 를 켜고 정책을 두지 않아 service_role(서버)만 읽는다.
  · 고르기 단계가 실패한 결과(pick_error)는 저장하지 않는다. 잠깐의 API 오류로 덜 맞는 색이 영구히 고정되는 것을 막는다.
  · Supabase 가 안 될 때도 분석은 멈추지 않는다: get 은 '없음'으로, put 은 이 서버 메모리에만 남기고 경고만 찍는다.
  · 추가 설치 없음 (requests 만 사용, Supabase REST 직접 호출).

환경변수 (Vercel · 로컬 .env)
  SUPABASE_URL               https://xxxx.supabase.co
  SUPABASE_SECRET_KEY        서버 키. 새 형식 sb_secret_… 또는 예전 service_role 키(eyJ…) 둘 다 된다.
                             (SUPABASE_SERVICE_ROLE_KEY · SUPABASE_SERVICE_KEY 이름도 받음. 프론트 astral 과 같은 이름)
                             공개 키(sb_publishable_ / anon)로는 동작하지 않는다(RLS).
  MAC_STORE=memory           Supabase 를 쓰지 않고 예전처럼 서버 메모리만 (로컬 시험용)

연결 (pipeline.py 두 군데):
  1) _DEFAULT_STORE = MemoryStore()
     →  from .store_supabase import store_from_env
        _DEFAULT_STORE = store_from_env() or MemoryStore()
  2) analyze_sentence 의 result = { ... } 안에 한 줄 추가
        "sentence": sentence,
"""
from __future__ import annotations

import copy
import logging
import os
from collections import OrderedDict

import requests

log = logging.getLogger("mac_color.store")

_KEY_ENV = ("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY")   # 앞에 있는 이름이 우선
_LOCAL_MAX = 256                      # 이 서버 안에 잠깐 들고 있는 결과 수 (같은 문장 반복 시 왕복 줄이기)


class SupabaseStore:
    def __init__(self, url: str, key: str, *, timeout: float = 4.0, session: requests.Session | None = None):
        self.base = url.rstrip("/") + "/rest/v1"
        self.timeout = timeout
        self.http = session or requests.Session()
        self.headers = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):                    # 예전 service_role 키(JWT)만 Bearer 로도 보낸다.
            self.headers["Authorization"] = f"Bearer {key}"   # 새 sb_secret_ 키는 JWT 가 아니라 apikey 헤더에만 넣는다
        self.local: OrderedDict[str, dict] = OrderedDict()

    # ------------------------------------------------------------ 엔진이 부르는 두 함수
    def get(self, k: str):
        if k in self.local:
            self.local.move_to_end(k)
            self._hit(k)
            return copy.deepcopy(self.local[k])
        try:
            r = self.http.get(f"{self.base}/mac_results", params={"select": "payload", "sentence_key": f"eq.{k}"},
                              headers=self.headers, timeout=self.timeout)
            r.raise_for_status()
            rows = r.json()
        except Exception as e:                       # 못 읽으면 새로 분석 (결과는 put 에서 먼저 쓴 값으로 맞춰짐)
            log.warning("mac_results 읽기 실패 → 새로 분석: %s", _why(e))
            return None
        if not rows:
            return None
        v = rows[0]["payload"]
        self._remember(k, v)
        self._hit(k)
        return copy.deepcopy(v)

    def put(self, k: str, v: dict) -> None:
        if v.get("pick_error"):
            log.warning("고르기 단계 오류(%s)가 있는 결과는 저장하지 않음 — 다음에 같은 문장이 오면 다시 분석", v["pick_error"])
            return
        body = {kk: vv for kk, vv in v.items() if kk != "cached"}
        ver = body.get("version") or {}
        chips = [c for c in (body.get("palette") or []) if isinstance(c, dict)][:4]
        row = {
            "sentence_key": k,
            "sentence": body.get("sentence"),                 # pipeline 이 결과에 넣어 준 입력 문장 (없으면 빈칸)
            "memory_summary": body.get("memory_summary"),
            **{f"chip{i + 1}_hex": _hex(chips[i]) if i < len(chips) else None for i in range(4)},
            "chip_names": [c.get("name") for c in chips] or None,
            "specimen_hash": body.get("specimen_hash"),
            "specimen_code": body.get("specimen_code"),
            "payload": body,
            "pipeline_version": ver.get("pipeline"),
            "versions": ver or None,
        }
        try:
            r = self.http.post(f"{self.base}/mac_results", params={"on_conflict": "sentence_key"}, json=row,
                               headers={**self.headers, "Prefer": "resolution=ignore-duplicates,return=representation"},
                               timeout=self.timeout)
            r.raise_for_status()
            inserted = bool(r.json())               # 빈 목록 = 이미 다른 서버가 먼저 넣음
        except Exception as e:
            log.warning("mac_results 저장 실패 → 이 서버 메모리에만 보관: %s", _why(e))
            self._remember(k, body)
            return

        if inserted:
            self._remember(k, body)
            self._log_pending(body)
            return
        winner = self.get(k)                          # 먼저 들어간 결과로 맞춘다 (같은 문장 = 같은 칩)
        if winner:
            cached = v.get("cached", False)
            v.clear()
            v.update(winner)
            v["cached"] = cached

    # ------------------------------------------------------------ 부가
    def _hit(self, k: str) -> None:
        try:
            self.http.post(f"{self.base}/rpc/mac_result_hit", json={"p_key": k}, headers=self.headers, timeout=2.0)
        except Exception:
            pass                                      # 횟수는 통계일 뿐 — 실패해도 무시

    def _log_pending(self, body: dict) -> None:
        words_v = (body.get("version") or {}).get("words", "")
        rows = [{"text": p.get("text"), "axis": p.get("axis"), "color": p.get("color") or "",
                 "method": "llm_pick" if p.get("method") == "gemini_pick" else p.get("method"),
                 "words_version": words_v}
                for p in body.get("pending") or [] if p.get("text")]
        if not rows:
            return
        try:
            r = self.http.post(f"{self.base}/rpc/log_pending", json={"p_rows": rows}, headers=self.headers,
                               timeout=self.timeout)
            r.raise_for_status()
        except Exception as e:
            log.warning("mac_pending 기록 실패 (분석 결과에는 영향 없음): %s", _why(e))

    def _remember(self, k: str, v: dict) -> None:
        self.local[k] = copy.deepcopy(v)
        self.local.move_to_end(k)
        while len(self.local) > _LOCAL_MAX:
            self.local.popitem(last=False)


def _hex(chip: dict) -> str | None:
    h = str(chip.get("hex") or "").strip().upper()
    return h if len(h) == 7 and h.startswith("#") else None


def _why(e: Exception) -> str:
    """오류 원인만 짧게 (키·주소는 찍지 않음)."""
    if isinstance(e, requests.HTTPError) and e.response is not None:
        try:
            msg = e.response.json().get("message", "")
        except Exception:
            msg = e.response.text[:120]
        return f"HTTP {e.response.status_code} {msg}"
    return type(e).__name__


def store_from_env() -> SupabaseStore | None:
    """환경변수가 다 있으면 SupabaseStore, 아니면 None (호출한 쪽이 MemoryStore 로)."""
    try:
        from .clients import load_env_file
        load_env_file()
    except Exception:
        pass
    if os.getenv("MAC_STORE", "").lower() == "memory":
        return None
    url = os.getenv("SUPABASE_URL", "").strip()
    key = next((os.getenv(n, "").strip() for n in _KEY_ENV if os.getenv(n, "").strip()), "")
    if not url or not key or "여기에" in key:
        log.info("SUPABASE_URL / SUPABASE_SECRET_KEY 가 없어 결과를 서버 메모리에만 저장")
        return None
    return SupabaseStore(url, key)
