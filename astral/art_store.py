"""Durable, fail-closed generation budget.

ArtStore         : SQLite 파일 (디스크가 있는 Railway 등 단일 서버)
SupabaseArtStore : Supabase (Vercel — 함수 인스턴스마다 디스크가 따로라 SQLite 로는 한도를 지킬 수 없음)
open_store(config) 가 public_art.deployment_config() 의 'store' 값으로 고른다.
두 저장소 모두 '외부 이미지 API 호출 전에' 사용량을 먼저 기록한다 (실패·시간초과도 1회로 셈).
"""
import json
import os
from collections import OrderedDict
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from contextlib import contextmanager
import sqlite3
import time
from pathlib import Path
from uuid import uuid4


class ArtLimitError(Exception):
    pass


class ArtStore:
    def __init__(self, path):
        self.path = str(Path(path))
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, image_key TEXT NOT NULL,
                    started REAL NOT NULL, active_until REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS attempt_time ON attempts(started);
                CREATE TABLE IF NOT EXISTS images (
                    image_key TEXT PRIMARY KEY, expires REAL NOT NULL, payload TEXT NOT NULL
                );
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute('PRAGMA secure_delete=ON')
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, owner, image_key, daily_limit=20, total_limit=20):
        """Commit the charge BEFORE provider I/O; failures/timeouts still consume it."""
        now = time.time()
        day_start = int(now // 86400) * 86400  # Explicit UTC day.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM images WHERE expires <= ?', (now,))
            cached = db.execute('SELECT payload FROM images WHERE image_key=?', (image_key,)).fetchone()
            if cached:
                return None, {**json.loads(cached[0]), 'cached': True}
            if db.execute('SELECT 1 FROM attempts WHERE active_until>? LIMIT 1', (now,)).fetchone():
                raise ArtLimitError('다른 작품을 생성 중입니다. 잠시 후 다시 눌러 주세요.')
            total = db.execute('SELECT count(*) FROM attempts').fetchone()[0]
            today = db.execute('SELECT count(*) FROM attempts WHERE started>=?', (day_start,)).fetchone()[0]
            personal = db.execute('SELECT count(*) FROM attempts WHERE owner=? AND started>=?', (owner, day_start)).fetchone()[0]
            retries = db.execute('SELECT count(*) FROM attempts WHERE image_key=?', (image_key,)).fetchone()[0]
            if total >= total_limit or today >= daily_limit:
                raise ArtLimitError('서비스의 전통화 생성 한도에 도달했습니다. 운영자에게 문의해 주세요.')
            if personal >= 3 or retries >= 3:
                raise ArtLimitError('오늘 이용 가능한 전통화 생성 횟수에 도달했습니다.')
            reservation = uuid4().hex
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?)', (reservation, owner, image_key, now, now + 300))
            return reservation, None

    def cleanup(self):
        with self.connect() as db:
            db.execute('DELETE FROM images WHERE expires<=?', (time.time(),))

    def finish(self, reservation, image_key, payload=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if payload is not None:
                db.execute('DELETE FROM images WHERE expires<=?', (time.time(),))
                db.execute('INSERT OR REPLACE INTO images VALUES (?,?,?)',
                           (image_key, time.time()+3600, json.dumps(payload, ensure_ascii=False)))
                db.execute('''DELETE FROM images WHERE image_key NOT IN
                              (SELECT image_key FROM images ORDER BY expires DESC LIMIT 4)''')
            db.execute('UPDATE attempts SET active_until=0 WHERE id=?', (reservation,))


# ---------------------------------------------------------------------------
# Vercel 용: Supabase 에 한도 기록 (sql/astral_art_limits.sql 의 함수 2개 사용)
# ---------------------------------------------------------------------------
_LIMIT_MESSAGES = {
    'busy': '다른 작품을 생성 중입니다. 잠시 후 다시 눌러 주세요.',
    'service_limit': '서비스의 전통화 생성 한도에 도달했습니다. 운영자에게 문의해 주세요.',
    'personal_limit': '오늘 이용 가능한 전통화 생성 횟수에 도달했습니다.',
}
# 완성된 그림은 같은 함수 인스턴스 안에서만 1시간 재사용 (DB 에 수 MB 이미지를 넣지 않음)
_MEMORY_IMAGES = OrderedDict()
_MEMORY_LOCK = Lock()


class SupabaseArtStore:
    def __init__(self, url, key, timeout=8):
        self.url = url.rstrip('/')
        self.timeout = timeout
        self.headers = {'apikey': key, 'Content-Type': 'application/json', 'User-Agent': 'AstralColor/1.0'}
        if key.startswith('eyJ'):            # 예전 service_role JWT 키만 Authorization 에도 넣는다
            self.headers['Authorization'] = 'Bearer ' + key

    def _rpc(self, name, body):
        request = Request(f'{self.url}/rest/v1/rpc/{name}', data=json.dumps(body).encode('utf-8'),
                          headers=self.headers, method='POST')
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raise RuntimeError(f'Supabase rpc {name} HTTP {exc.code}') from None
        except URLError:
            raise RuntimeError(f'Supabase rpc {name} connection failed') from None
        return json.loads(raw) if raw else None

    def reserve(self, owner, image_key, daily_limit=20, total_limit=20):
        now = time.time()
        with _MEMORY_LOCK:
            for key in [k for k, (exp, _) in _MEMORY_IMAGES.items() if exp <= now]:
                _MEMORY_IMAGES.pop(key, None)
            if image_key in _MEMORY_IMAGES:
                return None, {**_MEMORY_IMAGES[image_key][1], 'cached': True}
        result = self._rpc('astral_art_reserve', {'p_owner': owner, 'p_key': image_key,
                                                  'p_daily': daily_limit, 'p_total': total_limit})
        if not isinstance(result, dict):
            raise RuntimeError('Supabase rpc astral_art_reserve: unexpected response')
        if result.get('error'):
            raise ArtLimitError(_LIMIT_MESSAGES.get(result['error'], _LIMIT_MESSAGES['service_limit']))
        if not result.get('reservation'):
            raise RuntimeError('Supabase rpc astral_art_reserve: no reservation')
        return result['reservation'], None

    def finish(self, reservation, image_key, payload=None):
        if payload is not None:
            with _MEMORY_LOCK:
                _MEMORY_IMAGES[image_key] = (time.time() + 3600, payload)
                while len(_MEMORY_IMAGES) > 4:
                    _MEMORY_IMAGES.popitem(last=False)
        if reservation:
            self._rpc('astral_art_finish', {'p_id': reservation})


def open_store(config):
    if config.get('store') == 'supabase':
        return SupabaseArtStore(config['supabase_url'], config['supabase_key'])
    return ArtStore(config['db_path'])
