"""Explicit HTTPS deployment settings and anonymous, signed browser sessions."""
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

COOKIE_NAME = '__Host-astral-session'


def supabase_credentials():
    """서버 전용 Supabase 주소·키. 팀 공용 이름(SUPABASE_SERVICE_ROLE_KEY)을 먼저 본다."""
    url = os.environ.get('SUPABASE_URL', '').strip().rstrip('/')
    key = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_SECRET_KEY') or '').strip()
    return url, key


def _valid_origin(origin):
    parsed = urlsplit(origin)
    return (parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
            and not parsed.path and not parsed.query and not parsed.fragment)


def deployment_config():
    """공개(운영) 전통화 생성 설정. 조건이 하나라도 빠지면 None → 생성 버튼이 꺼진다 (fail-closed).

    · Railway 등 디스크가 있는 서버: 한도를 SQLite(ASTRAL_ART_DB_PATH)에 저장 (기존 방식)
    · Vercel(VERCEL 환경변수 자동 설정): 디스크가 없으므로 한도를 Supabase 에 저장
      (sql/astral_art_limits.sql 을 Supabase SQL Editor 에서 한 번 실행해야 함)
    """
    if os.environ.get('ASTRAL_PUBLIC_IMAGE_ENABLED') != '1':
        return None
    secret = os.environ.get('ASTRAL_SIGNING_SECRET', '')
    origin = os.environ.get('ASTRAL_PUBLIC_ORIGIN', '').rstrip('/')
    try:
        daily = int(os.environ.get('ASTRAL_IMAGE_DAILY_LIMIT', '20'))
        total = int(os.environ.get('ASTRAL_IMAGE_TOTAL_LIMIT', '20'))
    except ValueError:
        return None
    if len(secret) < 32 or not (1 <= daily <= 20 and 1 <= total <= 1000):
        return None
    if origin and not _valid_origin(origin):
        return None
    base = {'secret': secret.encode(), 'origin': origin, 'daily_limit': daily, 'total_limit': total}

    if os.environ.get('VERCEL'):
        url, key = supabase_credentials()
        if not url.startswith('https://') or not key:
            return None
        return {**base, 'store': 'supabase', 'supabase_url': url, 'supabase_key': key}

    path = Path(os.environ.get('ASTRAL_ART_DB_PATH', ''))
    root = Path(__file__).resolve().parent.parent
    try:
        valid = (origin and path.is_absolute() and path.parent.is_dir()
                 and not path.resolve().is_relative_to(root))
    except (ValueError, OSError):
        return None
    if not valid:
        return None
    return {**base, 'store': 'sqlite', 'db_path': str(path)}


def issue_session(config):
    body = f'{secrets.token_hex(24)}.{int(time.time())+86400}'
    signature = hmac.new(config['secret'], ('session:'+body).encode(), hashlib.sha256).hexdigest()
    return body+'.'+signature


def session_owner(token, config):
    try:
        nonce, expiry, signature = token.split('.')
        body = nonce+'.'+expiry
        expected = hmac.new(config['secret'], ('session:'+body).encode(), hashlib.sha256).hexdigest()
        if len(nonce) != 48 or int(expiry) <= time.time() or not hmac.compare_digest(expected, signature):
            return None
        return hashlib.sha256(nonce.encode()).hexdigest()
    except (AttributeError, ValueError):
        return None
