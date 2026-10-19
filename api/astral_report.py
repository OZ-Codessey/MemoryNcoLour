"""Complete Astral results from one request; no account or payment required."""
import json
import os
from http.cookies import SimpleCookie

from api.astral import handler as JsonHandler
from astral.public_art import COOKIE_NAME, deployment_config, issue_session, session_owner
from astral.engine import AstralInputError, calculate_report
from api.astral_art import make_art_package


def process_astral_report_request(data, owner=None):
    if not isinstance(data, dict):
        return 400, {"status": "error", "error": "출생정보 형식을 확인해 주세요."}
    try:
        report = calculate_report(data)
        report['artwork'] = make_art_package(report, owner=owner)
        return 200, report
    except AstralInputError as exc:
        return 400, {"status": "error", "error": str(exc)}


def report_session(headers):
    """Vercel 공개 전통화용: 기존 세션 쿠키가 유효하면 그대로, 아니면 새로 발급. → (owner, 새 쿠키 값 | None)"""
    config = deployment_config() if os.environ.get('VERCEL') else None
    if not config:
        return None, None
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get('Cookie') or '')
    except Exception:
        pass
    token = cookie.get(COOKIE_NAME)
    owner = session_owner(token.value, config) if token else None
    if owner:
        return owner, None
    fresh = issue_session(config)
    return session_owner(fresh, config), fresh


class handler(JsonHandler):
    def _send_json_cookie(self, status_code, payload, cookie_value=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if cookie_value:
            # __Host- 접두어: Secure·Path=/ 필수, 다른 도메인과 공유 안 됨. HttpOnly → JS 로 읽을 수 없음
            self.send_header("Set-Cookie", f"{COOKIE_NAME}={cookie_value}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=86400")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"status": "error", "error": "유효하지 않은 요청입니다."})
            return
        cookie_value = None
        try:
            owner, cookie_value = report_session(self.headers)
            status, report = process_astral_report_request(data, owner=owner)
        except Exception:
            status, report = 500, {"status": "error", "error": "계산 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}
        self._send_json_cookie(status, report, cookie_value if status == 200 else None)
