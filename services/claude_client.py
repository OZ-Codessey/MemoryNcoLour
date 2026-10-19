"""
================================================================================
MaC — Claude 텍스트 클라이언트 (Codyssey 공개 API · Anthropic Messages 규격)
================================================================================
텍스트 단계(문장 → 팔레트 계획)는 Claude, 그림 단계만 Gemini 를 쓴다.
backend KS 엔진의 services/mac_color/clients.py 에서 Claude 부분만 가볍게 옮겼다.

환경변수 (.env 또는 Vercel 환경변수 — 이미 있는 값이 우선)
  ANTHROPIC_API_KEY   필수. Codyssey 키(sk-cody-…)
  ANTHROPIC_BASE_URL  Codyssey 면 https://copa.codyssey.kr  (없으면 Anthropic 공식 주소)
  MAC_CLAUDE_MODEL    기본: Codyssey 주소면 claude-sonnet-4, 아니면 claude-sonnet-5-5

동작
  - requests 로 직접 호출 (새 패키지 없음). 키·문장은 로그에 남기지 않는다.
  - 429·5xx·연결 실패는 한 번만 짧게 다시 시도.
  - 400(옵션 거부)이면 구조화 출력 → 없이 순서로 내려가고, 통한 방식을 기억해 다음부터 바로 쓴다.
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _is_placeholder(v: str) -> bool:
    return not v or "여기에" in v or v.startswith("<") or v.endswith("...") or v.lower() in ("your_key", "changeme")


def load_env_file(path: Path | None = None) -> None:
    """저장소 맨 위 .env 를 읽는다 (python-dotenv 없이도). 이미 있는 환경변수는 덮어쓰지 않는다."""
    p = Path(path) if path else ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.split(" #", 1)[0].strip().strip('"').strip("'").strip()
        if k and not _is_placeholder(v) and (k not in os.environ or _is_placeholder(os.environ[k])):
            os.environ[k] = v


def api_key(name: str) -> str | None:
    load_env_file()
    v = (os.getenv(name) or "").strip()
    if _is_placeholder(v):
        return None
    if not v.isascii() or any(c.isspace() for c in v):
        raise RuntimeError(f"{name} 값에 키가 아닌 글자(한글·공백 등)가 섞여 있음 — .env 의 그 줄을 확인")
    return v


load_env_file()
CLAUDE_BASE = (os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/").removesuffix("/v1")
CLAUDE_URL = CLAUDE_BASE + "/v1/messages"
CLAUDE_MODEL = os.getenv("MAC_CLAUDE_MODEL") or ("claude-sonnet-4" if "codyssey" in CLAUDE_BASE else "claude-sonnet-5-5")


class ClaudeError(RuntimeError):
    pass


def claude_safe_schema(schema: dict) -> dict:
    """구조화 출력용으로 스키마를 다듬는다: 모든 object 에 additionalProperties:false,
    지원이 불확실한 minItems/maxItems 는 뺀다 (개수는 서버의 validate 가 다시 검사)."""
    s = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            node.pop("minItems", None)
            node.pop("maxItems", None)
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(s)
    return s


# 0 = 구조화 출력 / 1 = 없이 (지시문으로 JSON 요청). 통한 단계를 기억한다.
_VARIANTS = [dict(structured=True), dict(structured=False)]
_variant_start = 0


def _body(user: str, system: str | None, schema: dict | None, model: str | None, max_tokens: int,
          structured: bool) -> dict:
    body = {"model": model or CLAUDE_MODEL, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "user", "content": user}]}
    if system:
        body["system"] = system
    if schema is not None and structured:
        body["output_config"] = {"format": {"type": "json_schema", "schema": claude_safe_schema(schema)}}
    return body


def parse_json_text(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    a, b = t.find("{"), t.rfind("}")
    return json.loads(t[a:b + 1] if a >= 0 and b > a else t)


def claude_json(user: str, *, system: str | None = None, schema: dict | None = None, model: str | None = None,
                max_tokens: int = 3000, timeout: float = 60.0) -> dict:
    """Claude 에 (system, user) 를 보내고 JSON(dict) 하나를 받는다."""
    import requests
    key = api_key("ANTHROPIC_API_KEY")
    if not key:
        raise ClaudeError("ANTHROPIC_API_KEY 없음 (.env 확인)")
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    global _variant_start
    v, transient_left, err = _variant_start, 1, ""
    while True:
        body = _body(user, system, schema, model, max_tokens, **_VARIANTS[v])
        try:
            r = requests.post(CLAUDE_URL, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            r, err = None, type(e).__name__
        if r is not None and r.status_code == 200:
            out = r.json()
            if out.get("stop_reason") in ("max_tokens", "refusal"):
                raise ClaudeError(f"Claude 응답이 끝까지 오지 않음 (stop_reason={out.get('stop_reason')})")
            text = "".join(c.get("text", "") for c in out.get("content", []) if c.get("type") == "text")
            _variant_start = v
            return parse_json_text(text)
        if r is not None and r.status_code == 400 and v + 1 < len(_VARIANTS):
            v += 1
            continue
        transient = r is None or r.status_code in (429, 529) or r.status_code >= 500
        if transient and transient_left > 0:
            transient_left -= 1
            try:
                wait = min(float(r.headers.get("retry-after", 2)), 5.0) if r is not None else 2.0
            except ValueError:
                wait = 2.0
            time.sleep(wait)
            continue
        if r is None:
            raise ClaudeError(f"Claude 연결 실패 ({err})")
        try:
            kind = r.json().get("error", {}).get("type", "")
        except Exception:
            kind = ""
        raise ClaudeError(f"Claude 호출 실패: HTTP {r.status_code} {kind}".strip())


def label() -> str:
    return f"claude:{CLAUDE_MODEL}"
