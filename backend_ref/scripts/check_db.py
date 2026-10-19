"""check_db.py — Supabase mac_results 저장이 되는지 단계별로 확인한다 (키 값은 화면에 찍지 않는다).

실행 (backend 레포 맨 위에서)
  python scripts/check_db.py "시험할 문장"

보는 것
  1) .env 의 SUPABASE_URL · SUPABASE_SECRET_KEY 를 읽었는지, 엔진이 어떤 저장소를 쓰는지
  2) 키로 mac_results 테이블에 접근되는지 (HTTP 상태)
  3) 문장을 분석해 저장되는지, 고르기 오류(pick_error)로 저장이 건너뛰어졌는지
  4) DB 에 그 문장 줄이 실제로 생겼는지
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.INFO, format="   [로그] %(message)s")

from services.mac_color import keys, pipeline  # noqa: E402
from services.mac_color.store_supabase import SupabaseStore, _KEY_ENV  # noqa: E402


def kind(key: str) -> str:
    if key.startswith("sb_secret_"):
        return "새 Secret 키 (sb_secret_…) — 맞음"
    if key.startswith("sb_publishable_"):
        return "Publishable(공개) 키 — 안 됨. Secret 키로 바꿀 것"
    if key.startswith("eyJ"):
        return "예전 JWT 키 (eyJ…) — service_role 이면 됨, anon 이면 안 됨"
    return "알 수 없는 형식"


def main() -> int:
    if len(sys.argv) < 2:
        print('사용법: python scripts/check_db.py "시험할 문장"')
        return 2
    sentence = sys.argv[1]

    print("1) 환경변수와 저장소")
    url = os.getenv("SUPABASE_URL", "").strip()
    name, key = next(((n, os.getenv(n, "").strip()) for n in _KEY_ENV if os.getenv(n, "").strip()), ("", ""))
    print(f"   SUPABASE_URL      : {'있음 (' + url.split('//')[-1].split('.')[0][:6] + '…)' if url else '없음'}")
    print(f"   서버 키           : {name + ' — ' + kind(key) if key else '없음 (SUPABASE_SECRET_KEY)'}")
    if os.getenv("MAC_STORE", "").lower() == "memory":
        print("   MAC_STORE=memory 가 켜져 있어 Supabase 를 쓰지 않음 → .env 에서 지울 것")
    store = pipeline._DEFAULT_STORE
    print(f"   엔진 저장소       : {type(store).__name__}")
    if not isinstance(store, SupabaseStore):
        print("\n→ 원인: 엔진이 서버 메모리(MemoryStore)를 쓰고 있어 프로그램이 끝나면 결과가 사라짐.")
        print("   .env 가 backend 레포 맨 위에 있는지, 두 줄 이름에 오타가 없는지, pipeline.py 를 덮어썼는지 확인.")
        return 1

    print("\n2) 테이블 접근")
    try:
        r = store.http.get(f"{store.base}/mac_results", params={"select": "sentence_key", "limit": "1"},
                           headers=store.headers, timeout=10)
    except Exception as e:
        print(f"   연결 실패: {type(e).__name__}")
        print("\n→ 원인: SUPABASE_URL 주소가 틀렸거나 인터넷·방화벽 문제. https://프로젝트ID.supabase.co 형식인지 확인.")
        return 1
    print(f"   HTTP {r.status_code}")
    if r.status_code != 200:
        msg = {401: "키가 틀렸거나 공개 키임", 403: "권한 없음 — Secret 키가 아님", 404: "mac_results 테이블이 없음 (이름 확인: mac_results)"}
        print(f"\n→ 원인: {msg.get(r.status_code, r.text[:200])}")
        return 1

    print("\n3) 분석")
    res = pipeline.analyze_sentence(sentence)
    print(f"   cached={res['cached']}  code={res['specimen_code']}")
    if res.get("pick_error"):
        print(f"   pick_error={res['pick_error']}")
        print("\n→ 원인: 고르기 단계(Claude) 호출이 실패한 결과라 일부러 저장하지 않음.")
        print("   ANTHROPIC_API_KEY · ANTHROPIC_BASE_URL · 요청 한도를 확인하고 다시 실행.")
        return 1

    print("\n4) DB 확인")
    k = keys.sentence_key(sentence)
    r = store.http.get(f"{store.base}/mac_results",
                       params={"select": "sentence,chip1_hex,chip2_hex,chip3_hex,chip4_hex,specimen_code,hits",
                               "sentence_key": f"eq.{k}"}, headers=store.headers, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    if rows:
        row = rows[0]
        print(f"   저장됨: {row['specimen_code']}  칩 {row['chip1_hex']} {row['chip2_hex']} {row['chip3_hex']} {row['chip4_hex']}  hits={row['hits']}")
        print("   문장 열   : " + ("있음" if row.get("sentence") else "비어 있음 → pipeline.py 를 덮어썼는지 확인"))
        print("\n→ 정상. 같은 명령을 한 번 더 실행하면 cached=True 가 나온다.")
        return 0
    print(f"   줄 없음 (HTTP {r.status_code})")
    print("\n→ 원인: 저장 요청이 실패함. 위 [로그] 줄의 'mac_results 저장 실패 … HTTP ___' 메시지를 확인.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
