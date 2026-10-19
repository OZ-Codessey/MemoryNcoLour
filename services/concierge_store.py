"""
컨시어지 문의 저장 (Supabase public.concierge_inquiries — sql/concierge_inquiries.sql)
- 서버 전용 (SUPABASE_URL · SUPABASE_SERVICE_ROLE_KEY). 키는 브라우저로 나가지 않는다.
- 실패해도 예외를 밖으로 던지지 않고 False — 메일 발송·사용자 응답을 막지 않는다.
"""
import os

import requests

TABLE = "concierge_inquiries"
TIMEOUT = 5


def _cfg():
    url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    return (url, key) if url and key else (None, None)


def save_inquiry(client_name: str, contact: str, request_type: str, message: str, email_sent: bool) -> bool:
    url, key = _cfg()
    if not url:
        return False
    headers = {"apikey": key, "Content-Type": "application/json", "Prefer": "return=minimal"}
    if key.startswith("eyJ"):                      # 예전 service_role(JWT) 키만 Authorization 에도
        headers["Authorization"] = f"Bearer {key}"
    row = {"client_name": client_name or None, "contact": contact, "request_type": request_type or None,
           "message": message, "email_sent": bool(email_sent)}
    try:
        r = requests.post(f"{url}/rest/v1/{TABLE}", headers=headers, json=row, timeout=TIMEOUT)
        r.raise_for_status()
        return True
    except Exception as err:
        print(f"[Concierge] save failed: {err}")
        return False
