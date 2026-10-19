"""사주 전통화 — Vercel 공개 생성 경로 시험 (오프라인: Supabase·이미지 API 는 가짜). 실행: python tests/test_astral_vercel.py"""
import base64
import io
import os
import sys
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ENV = {"VERCEL": "1", "ASTRAL_PUBLIC_IMAGE_ENABLED": "1", "ASTRAL_IMAGE_ENABLED": "1",
       "ASTRAL_IMAGE_PROVIDER": "codyssey", "ASTRAL_IMAGE_MODEL": "gpt-image-2",
       "ASTRAL_SIGNING_SECRET": "x" * 40, "ASTRAL_IMAGE_DAILY_LIMIT": "20", "ASTRAL_IMAGE_TOTAL_LIMIT": "50",
       "SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_test",
       "ANTHROPIC_BASE_URL": "https://copa.codyssey.kr", "ANTHROPIC_API_KEY": "sk-cody-test"}
for k in ("CODYSSEY_API_KEY", "OPENAI_API_KEY", "ASTRAL_PUBLIC_ORIGIN", "SUPABASE_SECRET_KEY"):
    os.environ.pop(k, None)
os.environ.update(ENV)

import astral.art_store as store_mod  # noqa: E402
from api import astral_art, astral_report  # noqa: E402

ROWS = []


def fake_rpc(self, name, body):
    """sql/astral_art_limits.sql 과 같은 규칙의 가짜 Supabase."""
    import time
    now = time.time()
    if name == "astral_art_finish":
        for r in ROWS:
            if r["id"] == body["p_id"]:
                r["active_until"] = 0
        return None
    day = int(now // 86400) * 86400
    if any(r["active_until"] > now for r in ROWS):
        return {"error": "busy"}
    if len(ROWS) >= body["p_total"] or sum(r["started"] >= day for r in ROWS) >= body["p_daily"]:
        return {"error": "service_limit"}
    if (sum(r["owner"] == body["p_owner"] and r["started"] >= day for r in ROWS) >= 3
            or sum(r["image_key"] == body["p_key"] for r in ROWS) >= 3):
        return {"error": "personal_limit"}
    rid = str(uuid.uuid4())
    ROWS.append({"id": rid, "owner": body["p_owner"], "image_key": body["p_key"], "started": now, "active_until": now + 300})
    return {"reservation": rid}


store_mod.SupabaseArtStore._rpc = fake_rpc


def png_b64(w=1024, h=1536, noise=False):
    a = np.random.default_rng(0).integers(0, 256, (h, w, 3), dtype=np.uint8) if noise else np.full((h, w, 3), 200, np.uint8)
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


BIRTH = {"gender": "female", "calendar_type": "solar", "birth_date": "1995-03-14",
         "birth_time": "09:30", "birth_country": "KR", "birth_city": "서울특별시"}


def _report(cookie_header=""):
    owner, fresh = astral_report.report_session({"Cookie": cookie_header})
    status, report = astral_report.process_astral_report_request(BIRTH, owner=owner)
    return status, report, owner, fresh


def test_config_on_vercel():
    assert astral_art.art_configuration()["configured"], "Vercel + Supabase 설정이면 생성 버튼이 켜져야 함"
    assert astral_art.image_api_key("codyssey") == "sk-cody-test", "Codyssey 공용 키 사용"


def test_full_flow_and_limits():
    ROWS.clear()
    store_mod._MEMORY_IMAGES.clear()
    status, report, owner, fresh = _report()
    assert status == 200 and fresh and owner, (status, report.get("error"))
    assert report["artwork"]["configured"]
    cookie = f"{astral_art.COOKIE_NAME}={fresh}"
    # 쿠키로 소유자 확인
    config = astral_art.deployment_config()
    assert astral_art.request_owner({"Cookie": cookie}, config) == owner
    assert astral_art.request_owner({"Cookie": ""}, config) is None
    assert astral_art.same_origin({"Origin": "https://evil.example", "Host": "memoryncolour.vercel.app"}, config) is False
    assert astral_art.same_origin({"Origin": "https://memoryncolour.vercel.app", "Host": "memoryncolour.vercel.app"}, config)
    # 생성 → 같은 요청은 메모리 캐시
    gen = lambda spec: png_b64()
    st, res = astral_art.process_astral_art_request({"ticket": report["artwork"]["ticket"]}, production=True, owner=owner, generator=gen)
    assert st == 200 and res["status"] == "success" and not res["cached"], (st, res.get("error"))
    st, res = astral_art.process_astral_art_request({"ticket": report["artwork"]["ticket"]}, production=True, owner=owner, generator=gen)
    assert st == 200 and res["cached"]
    assert len(ROWS) == 1 and ROWS[0]["active_until"] == 0
    # 다른 사람 티켓은 거부
    st, res = astral_art.process_astral_art_request({"ticket": report["artwork"]["ticket"]}, production=True, owner="someone-else", generator=gen)
    assert st == 400
    # 실패해도 1회로 셈 + 1인 하루 3장 한도
    def boom(spec):
        raise ValueError("bad image")
    store_mod._MEMORY_IMAGES.clear()
    st, _ = astral_art.process_astral_art_request({"ticket": report["artwork"]["ticket"]}, production=True, owner=owner, generator=boom)
    assert st == 502 and len(ROWS) == 2
    st, _ = astral_art.process_astral_art_request({"ticket": report["artwork"]["ticket"]}, production=True, owner=owner, generator=gen)
    assert st == 200 and len(ROWS) == 3
    store_mod._MEMORY_IMAGES.clear()
    st, res = astral_art.process_astral_art_request({"ticket": report["artwork"]["ticket"]}, production=True, owner=owner, generator=gen)
    assert st == 429, (st, res)


def test_missing_supabase_fails_closed():
    key = os.environ.pop("SUPABASE_SERVICE_ROLE_KEY")
    try:
        assert astral_art.deployment_config() is None
        assert not astral_art.art_configuration()["configured"]
    finally:
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = key


def test_response_fits_vercel_limit():
    big = png_b64(1024, 1536, noise=True)
    assert len(big) > astral_art.MAX_RESPONSE_B64
    small = astral_art.fit_response_size(big)
    assert len(small) <= astral_art.MAX_RESPONSE_B64, len(small)
    img = Image.open(io.BytesIO(base64.b64decode(small)))
    assert img.size == (1024, 1536) and img.format == "PNG"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
