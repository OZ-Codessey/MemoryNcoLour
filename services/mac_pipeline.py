"""
================================================================================
MaC v2 파이프라인 — 기억 문장 → (Claude) 팔레트 계획 → (Gemini) 색면추상화 → 칩 4개
================================================================================
api/analyze.py 가 부른다 (MAC_ENGINE=v2, 기본값).

반환 (run_v2):
  {
    "memory_summary": str,
    "palette": [칩 4개 — 사이트 모달·메일·DB 가 쓰는 기존 형식 그대로
                {color_name, hex, weight, area_ratio, reason, role, name_source}],
    "painting_base64": str | None,     # JPEG base64 (그림 실패 시 None)
    "painting_mime": "image/jpeg",
    "plan": {...},                     # Claude 팔레트 계획 (검증·정규화된 것)
    "paint_info": {...} | None,        # 모델·시간·재시도·구도
    "paint_error": str | None,         # 그림이 실패했을 때 이유 (칩은 계획 색으로 대신)
    "pipeline_version": "v2",
  }

그림이 실패하면(혼잡 503 이 모든 모델에서 계속 등): 칩은 '계획 팔레트의 면적 상위 4색'으로 대신 채우고
paint_error 에 이유를 남긴다 → 사용자는 그림 없이 칩·요약은 받는다.

환경변수: ANTHROPIC_API_KEY · ANTHROPIC_BASE_URL · MAC_CLAUDE_MODEL (텍스트)
          GEMINI_API_KEY · GEMINI_IMAGE_MODEL · GEMINI_IMAGE_FALLBACKS (그림)
          MAC_PAINT_TIMEOUT (요청 1번 대기 초, 기본 90) · MAC_PAINT_RETRIES (기본 2)
"""
from __future__ import annotations

import base64
import io
import os
import time

from prompts import color_prompt_v2 as v2
from prompts.paint_prompt import build_paint_request
from services.claude_client import claude_json
from services.ks_names import apply_ks_names
from services.measure import extract_chips
from services.painter import PaintError, load_references, paint

PIPELINE_VERSION = "v2"


class PlanFailed(Exception):
    pass


def make_plan(memory: str, log=print) -> dict:
    """Claude 로 팔레트 계획. 형식이 틀리면 1번만 다시."""
    last = None
    for attempt in range(2):
        raw = claude_json(v2.build_user_prompt(memory), system=v2.build_system_prompt(), schema=v2.RESPONSE_SCHEMA)
        ok, errors, plan = v2.validate_and_normalize(raw)
        if ok:
            if plan["fixes"]:
                log(f"[v2] 팔레트 자동 수정: {'; '.join(plan['fixes'])}")
            return plan
        last = errors
        log(f"[v2] 팔레트 형식 오류 (시도 {attempt + 1}): {errors}")
    raise PlanFailed(f"팔레트 계획 형식 오류: {last}")


def _chip_out(c: dict) -> dict:
    return {"color_name": c.get("color_name") or c["hex"], "color_name_en": c.get("color_name_en"),
            "ks_kind": c.get("ks_kind"), "hex": c["hex"],
            "weight": c["ratio"], "area_ratio": c["ratio"], "share_in_painting": c.get("share"),
            "reason": c.get("reason") or "", "role": c.get("role"), "name_source": c.get("name_source")}


def _plan_top4(plan: dict) -> list[dict]:
    """그림이 없을 때: 계획 팔레트의 면적 상위 4색 (비율은 4색 합 100%로)."""
    top = sorted(plan["palette"], key=lambda c: -c["share"])[:4]
    tot = sum(c["share"] for c in top) or 1
    return apply_ks_names([{"color_name": c["color_name"], "hex": c["hex"], "weight": round(c["share"] / tot, 4),
                            "area_ratio": round(c["share"] / tot, 4), "reason": c.get("reason", ""),
                            "role": c.get("role"), "name_source": "plan_only"} for c in top])


def run_v2(memory: str, sentence_key: str, *, avoid_image: bytes | None = None,
           avoid_compositions: list[str] | None = None, paint_enabled: bool = True, log=print) -> dict:
    """avoid_image: 직전 그림(JPEG) — 있으면 '이것과 다르게' 첨부. avoid_compositions: 직전 그림들의 구도 힌트."""
    t0 = time.time()
    plan = make_plan(memory, log)
    log(f"[v2] 팔레트 {len(plan['palette'])}색 ({time.time() - t0:.0f}초)")
    out = {"memory_summary": plan["memory_summary"], "plan": plan, "pipeline_version": PIPELINE_VERSION,
           "painting_base64": None, "painting_mime": "image/jpeg", "paint_info": None, "paint_error": None}
    if not paint_enabled:
        out["palette"] = _plan_top4(plan)
        return out

    req = build_paint_request(plan, sentence_key, "4:5", avoid_compositions)
    try:
        img, info = paint(req, load_references(), avoid=avoid_image, log=log,
                          timeout=float(os.getenv("MAC_PAINT_TIMEOUT", "90")),
                          retries=int(os.getenv("MAC_PAINT_RETRIES", "2")))
    except PaintError as e:
        log(f"[v2] 그림 실패 → 계획 색으로 칩: {e}")
        out["palette"] = _plan_top4(plan)
        out["paint_error"] = str(e)[:300]
        return out

    info["composition"] = req["composition"]
    res = extract_chips(img, plan["palette"], seed=int(sentence_key[:8], 16))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    out.update(palette=[_chip_out(c) for c in res["chips"]], paint_info=info,
               painting_base64=base64.b64encode(buf.getvalue()).decode())
    log(f"[v2] 완료 {time.time() - t0:.0f}초 · 칩 " + " · ".join(f"{c['hex']} {round(c['ratio']*100)}%" for c in res["chips"]))
    return out
