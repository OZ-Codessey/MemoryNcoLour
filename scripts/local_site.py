"""
로컬에서 사이트 전체를 띄워 '기억 문장 → 결과 모달(그림 + 칩)'을 브라우저로 시험한다. Vercel CLI 없이.

실행 (MemoryNcoLour-main 에서):
    python scripts/local_site.py            # http://localhost:8000
    python scripts/local_site.py --port 8080

- 정적 파일(index.html · css · js · images …)은 그대로 제공한다.
- POST /api/analyze 는 api/analyze.py 의 process_request 를 그대로 부른다 (Vercel 과 같은 코드).
- /api/chat · /api/astral* 등 다른 API 는 이 서버에서 제공하지 않는다 (해당 기능은 vercel dev 또는 배포 후 시험).
- .env 를 읽는다. RESEND_API_KEY 가 없으면 메일은 보내지 않고, SUPABASE_* 가 없으면 저장도 건너뛴다.
"""
import argparse
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from services.claude_client import load_env_file  # noqa: E402

load_env_file()
import analyze  # noqa: E402  (api/analyze.py — .env 를 읽은 뒤 불러야 키가 잡힌다)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", ""):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/analyze"):
            return self._json(404, {"error": f"로컬 서버는 /api/analyze 만 제공합니다 ({self.path})"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return self._json(400, {"error": "유효하지 않은 JSON 데이터입니다."})
        try:
            code, payload = analyze.process_request(data)
        except Exception as e:                      # 서버 콘솔에 원인을 남기고 500
            import traceback
            traceback.print_exc()
            code, payload = 500, {"error": f"서버 내부 오류: {type(e).__name__}"}
        self._json(code, payload)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    print(f"MaC 로컬 사이트: http://localhost:{a.port}  (엔진 {analyze.MAC_ENGINE}, 종료 Ctrl+C)")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
