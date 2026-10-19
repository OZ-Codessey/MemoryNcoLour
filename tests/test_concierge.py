"""컨시어지 문의 시험 (오프라인: 메일·Supabase 는 가짜). 실행: python tests/test_concierge.py"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "api")]
os.environ.setdefault("RESEND_API_KEY", "re_test")
os.environ["CONCIERGE_NOTIFY_EMAIL"] = "admin@example.com"
import analyze  # noqa: E402

SENT, SAVED = [], []


class FakeEmails:
    fail = False

    @classmethod
    def send(cls, params):
        if cls.fail:
            raise RuntimeError("mail down")
        SENT.append(params)


analyze.resend.Emails = FakeEmails
analyze.RESEND_API_KEY = "re_test"
analyze.save_inquiry = lambda *a: SAVED.append(a) or True

BASE = {"type": "concierge_inquiry", "client_name": "홍길동<script>", "client_contact": "a@b.com",
        "subject": "bespoke", "message": "<a href='http://evil'>클릭</a>\n색을 의뢰하고 싶어요"}


def test_saves_and_escapes():
    SENT.clear(); SAVED.clear()
    code, res = analyze.process_request(dict(BASE))
    assert code == 200, res
    assert SENT[0]["to"] == ["admin@example.com"]
    html = SENT[0]["html"]
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<a href='http://evil'>" not in html and "&lt;a href=" in html
    assert SAVED and SAVED[0][1] == "a@b.com" and SAVED[0][4] is True       # email_sent=True 로 저장


def test_mail_fails_but_saved():
    SENT.clear(); SAVED.clear()
    FakeEmails.fail = True
    try:
        code, res = analyze.process_request(dict(BASE))
    finally:
        FakeEmails.fail = False
    assert code == 200 and SAVED[0][4] is False, res


def test_both_fail():
    FakeEmails.fail = True
    old = analyze.save_inquiry
    analyze.save_inquiry = lambda *a: False
    try:
        code, _ = analyze.process_request(dict(BASE))
    finally:
        FakeEmails.fail = False
        analyze.save_inquiry = old
    assert code == 500


def test_no_notify_address_saves_only():
    SENT.clear(); SAVED.clear()
    os.environ["CONCIERGE_NOTIFY_EMAIL"] = ""
    try:
        code, res = analyze.process_request(dict(BASE))
    finally:
        os.environ["CONCIERGE_NOTIFY_EMAIL"] = "admin@example.com"
    assert code == 200 and not SENT and SAVED[0][4] is False, res


def test_validation():
    assert analyze.process_request({**BASE, "message": ""})[0] == 400
    assert analyze.process_request({**BASE, "message": "가" * 3001})[0] == 400


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
