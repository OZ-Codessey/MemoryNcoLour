"""
================================================================================
MaC (Memory & Color) Architecture — Vercel Serverless Pipeline Handler
================================================================================
[설계 목적]
1. 비즈니스 로직과 모델 공급자 간의 완전한 결합 분리(services/llm_engine.py 연동).
2. 4색 HEX 및 면적비(0.45, 0.30, 0.15, 0.10) 가중치를 CIELAB 공간에서 벡터 합성하여
   개인 고유 암호 해시 'MaC-L.C.H' 도출.
3. 원본 emailTemplate.html의 다크 씰 토글(<details>/<summary>) 및 슬림 2x2 카드 복원.
4. CORS 프리플라이트 및 Resend 트랜잭션 메일 발송 처리.
5. Vercel Python 런타임이 실제로 인식하는 BaseHTTPRequestHandler 진입점 적용.
6. 4대 계층별 예외 처리 기준(400 유효성, 502 AI 재시도, HEX 정규식 검증, Non-blocking 이메일) 완비.
7. 컨시어지 문의 폼(Concierge@mac.ai.kr) 단일 관문 라우팅 분기 처리 완비.
8. Supabase 장기 기억 저장소 연동 (같은 문장 캐시 재사용 + 새 결과 저장).
10. [v2] MAC_ENGINE=v2(기본): 문장 → Claude 팔레트 4~8색 → Gemini 색면추상화 → 그림에서 칩 4개 측정
    (services/mac_pipeline.py). 응답에 painting(data URL) 추가. MAC_ENGINE=legacy 면 예전 방식(Gemini 가 4색 직접).
9. [신규 추가] 챗봇 "추억의 HEX 찾기" 분기(type="hex_finder"):
   이메일 없이 기억 문장만으로 4색 팔레트 + 해시를 즉시 반환 (메일 발송 없음).
   분석 로직은 run_memory_analysis()로 분리해 표본 발송 경로와 공유하되,
   HEX 찾기는 save=False로 DB에 저장하지 않습니다. (이미 저장된 문장은 읽기 전용 재사용)
================================================================================
"""

from http.server import BaseHTTPRequestHandler
from pathlib import Path
from dotenv import load_dotenv
import json
import math
import os
import re
import resend
import traceback
import unicodedata


# 환경 변수 로드
load_dotenv()

# Resend API Key 설정
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

# 분리된 프롬프트 빌더 및 고가용성 AI 추론 엔진 임포트
try:
    from prompts.color_prompt import build_system_prompt
    from services.llm_engine import analyze_memory_with_llm
except ImportError:
    from color_prompt import build_system_prompt
    from llm_engine import analyze_memory_with_llm

# 장기 기억 저장소 (Supabase mac_results) — 환경변수 미설정 시 내부에서 자동으로 건너뜀
try:
    from services.mac_memory import lookup_cached, save_result, sentence_key
except ImportError:
    from mac_memory import lookup_cached, save_result, sentence_key

# [v2] 팔레트 → 그림 → 칩 파이프라인. 저장소 맨 위를 import 경로에 넣어 prompts/·services/ 를 찾게 한다
import sys as _sys
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
MAC_ENGINE = os.getenv("MAC_ENGINE", "v2").strip().lower()
if MAC_ENGINE == "v2":
    from services.mac_pipeline import PIPELINE_VERSION as V2_VERSION, run_v2
# 칩 이름 = KS A 0011 한국어 색이름 (관용색에 가까우면 관용색이름, 아니면 계통색이름)
from services.ks_names import apply_ks_names
# 컨시어지 문의 기록 (Supabase concierge_inquiries)
from services.concierge_store import save_inquiry

# 같은 인스턴스가 살아 있는 동안 '직전 그림'을 기억해 다음 그림이 겹치지 않게 첨부한다
# (서버리스라 인스턴스가 바뀌면 비어 있음 — 그림 저장소(Supabase Storage)를 붙이면 거기서 가져오도록 바꿀 자리)
_LAST_PAINTING = {"jpeg": None, "composition": None}

# 정규식 패턴 사전 컴파일
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
HEX_COLOR_REGEX = re.compile(r"^#[0-9A-F]{6}$")

# 기억 문장 길이 기준
MEMORY_MIN_LEN = 10
MEMORY_MAX_LEN = 2000

# mac_results.pipeline_version 컬럼에 기록되는 값 (save_result의 6번째 인자).
# 표본 발송과 HEX 찾기가 같은 값을 쓰도록 통일합니다. (result_json에 값이 있으면 그쪽이 우선)
PIPELINE_VERSION = "legacy" if MAC_ENGINE != "v2" else "v2"


def normalize_memory(text) -> str:
    """캐시 키 일관성을 위해 기억 문장을 정규화 (유니코드 NFC, 연속 공백/줄바꿈 → 공백 하나, 양끝 제거)"""
    text = unicodedata.normalize("NFC", str(text or ""))
    return " ".join(text.split())


# ------------------------------------------------------------------------------
# 1. 색채 물리학 연산 엔진 (sRGB -> CIE XYZ -> CIELAB -> 가중 LCH 해시)
# ------------------------------------------------------------------------------
def hex_to_rgb(hex_str: str):
    """HEX(#RRGGBB)를 0.0 ~ 1.0 선형 전 RGB 값으로 변환"""
    hex_str = hex_str.lstrip("#")
    if len(hex_str) != 6:
        return 0.5, 0.5, 0.5
    return (
        int(hex_str[0:2], 16) / 255.0,
        int(hex_str[2:4], 16) / 255.0,
        int(hex_str[4:6], 16) / 255.0,
    )

def srgb_to_xyz(r: float, g: float, b: float):
    """sRGB 감마(γ=2.4) 역보정 후 D65 백색광 기준 CIE XYZ 변환"""
    def linearize(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    rl, gl, bl = linearize(r), linearize(g), linearize(b)
    X = rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375
    Y = rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750
    Z = rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041
    return X, Y, Z

def xyz_to_lab(X: float, Y: float, Z: float):
    """CIE XYZ를 인간 지각 균등 공간인 CIELAB(L*, a*, b*)으로 변환"""
    Xn, Yn, Zn = 0.95047, 1.00000, 1.08883
    xr, yr, zr = X / Xn, Y / Yn, Z / Zn

    def f(t):
        return t ** (1.0 / 3.0) if t > 0.008856 else (7.787 * t) + (16.0 / 116.0)

    fx, fy, fz = f(xr), f(yr), f(zr)
    L = (116.0 * fy) - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    return L, a, b

def compute_weighted_lch_hash(palette: list) -> str:
    """CIELAB 공간 내 가중 중심점을 구하고 고유 암호 해시(MaC-L.C.H) 산출"""
    total_weight = sum(item.get("weight", item.get("area_ratio", 0.25)) for item in palette)
    if total_weight <= 0:
        total_weight = 1.0

    weighted_L = 0.0
    weighted_a = 0.0
    weighted_b = 0.0

    for item in palette:
        w = item.get("weight", item.get("area_ratio", 0.25)) / total_weight
        r, g, b = hex_to_rgb(item.get("hex", "#888888"))
        X, Y, Z = srgb_to_xyz(r, g, b)
        L, a, b_val = xyz_to_lab(X, Y, Z)

        weighted_L += L * w
        weighted_a += a * w
        weighted_b += b_val * w

    chroma = math.sqrt(weighted_a ** 2 + weighted_b ** 2)
    hue_rad = math.atan2(weighted_b, weighted_a)
    hue_deg = math.degrees(hue_rad)
    if hue_deg < 0:
        hue_deg += 360.0

    l_int = int(round(weighted_L))
    c_int = int(round(chroma))
    h_int = int(round(hue_deg))

    return f"MaC-{l_int:02d}.{c_int:02d}.{h_int:03d}"


# ------------------------------------------------------------------------------
# 2. 데이터 형식 검증 유틸리티
# ------------------------------------------------------------------------------
def validate_palette_schema_and_hex(palette: list) -> bool:
    """[HEX 데이터 형식 오류 검증] 정확히 4개 색상인지 및 대문자 6자리 HEX(^#[0-9A-F]{6}$) 충족 여부 확인"""
    if not isinstance(palette, list) or len(palette) != 4:
        return False
    for chip in palette:
        if not isinstance(chip, dict):
            return False
        hex_code = str(chip.get("hex", "")).strip().upper()
        chip["hex"] = hex_code  # 일관되게 대문자로 보정
        if not HEX_COLOR_REGEX.match(hex_code):
            return False
    return True


def validate_memory_text(memory: str):
    """기억 문장 유효성 검사. 문제가 있으면 오류 메시지를, 정상이면 None을 반환"""
    if not memory or len(memory) < MEMORY_MIN_LEN:
        return "기억 문장을 최소 10자 이상 구체적으로 입력해 주세요."
    if len(memory) > MEMORY_MAX_LEN:
        return f"기억 문장은 {MEMORY_MAX_LEN}자 이내로 입력해 주세요."
    return None


# ------------------------------------------------------------------------------
# 3. 공용 분석 파이프라인 (캐시 조회 → AI 추론/재시도 → 해시 산출 → 저장)
# ------------------------------------------------------------------------------
def run_memory_analysis(memory: str, pipeline_version: str = PIPELINE_VERSION, save: bool = True):
    """
    기억 문장을 분석하여 dict를 반환합니다. 실패 시 None.
    save=False면 DB에 아무것도 쓰지 않습니다. (새 결과 저장 X, 캐시 조회수 증가 X)
    이미 저장된 문장은 읽기 전용으로 재사용합니다.
    반환: {result_json, palette, memory_summary, specimen_hash, from_cache}
    """
    result_json = None
    palette = []

    # 저장/조회 키가 흔들리지 않도록 항상 정규화된 문장을 사용
    memory = normalize_memory(memory)

    # [장기 기억] 같은 문장이 이미 저장돼 있으면 AI 호출 없이 저장된 결과를 재사용 (+조회수 증가)
    from_cache = False
    try:
        if save:
            cached = lookup_cached(memory)
        else:
            try:
                cached = lookup_cached(memory, count_hit=False)  # 읽기 전용 조회
            except TypeError:
                cached = lookup_cached(memory)  # mac_memory.py가 아직 count_hit을 지원하지 않는 경우
    except Exception as cache_err:
        print(f"[Cache Lookup Warning]: {cache_err}")
        cached = None

    # lookup_cached()는 저장된 payload(dict) 자체를 반환 (palette가 최상위에 있음)
    if isinstance(cached, dict) and validate_palette_schema_and_hex(cached.get("palette")):
        from_cache = True
        result_json = cached
        palette = cached["palette"]
        print("[Cache HIT]")
    elif cached:
        print("[Cache MISS] 저장된 payload의 palette 형식이 달라 재분석합니다.")
    else:
        print("[Cache MISS]")

    painting_b64, paint_error = None, None

    # [v2] Claude 팔레트 → Gemini 그림 → 그림에서 칩 4개 (챗봇 HEX 찾기(save=False)는 그림 없이 빠르게)
    if not from_cache and MAC_ENGINE == "v2":
        try:
            import base64 as _b64
            v2res = run_v2(memory, sentence_key(memory), paint_enabled=save,
                           avoid_image=_LAST_PAINTING["jpeg"],
                           avoid_compositions=[_LAST_PAINTING["composition"]] if _LAST_PAINTING["composition"] else None)
            palette = v2res["palette"]
            if validate_palette_schema_and_hex(palette):
                result_json = {"memory_summary": v2res["memory_summary"], "palette": palette,
                               "pipeline_version": v2res["pipeline_version"], "plan": v2res["plan"],
                               "paint_info": v2res["paint_info"], "paint_error": v2res["paint_error"]}
                painting_b64, paint_error = v2res["painting_base64"], v2res["paint_error"]
                if painting_b64:
                    _LAST_PAINTING["jpeg"] = _b64.b64decode(painting_b64)
                    _LAST_PAINTING["composition"] = (v2res["paint_info"] or {}).get("composition")
        except Exception as v2_err:
            print(f"[v2 Pipeline Failed]: {v2_err}")
            traceback.print_exc()

    # [예외 기준 2 & 3] AI 추론/JSON 파싱 및 HEX 검증 (실패 시 최대 1회 즉시 재호출)
    if not from_cache and MAC_ENGINE != "v2":
        system_prompt = build_system_prompt()
        full_prompt = f"{system_prompt}\n\n[입력된 사용자 기억 사연]\n\"{memory}\""

        for attempt in range(2):
            try:
                temp_result = analyze_memory_with_llm(full_prompt)
                if not isinstance(temp_result, dict):
                    temp_result = json.loads(temp_result)

                candidate_palette = temp_result.get("palette", [])
                if validate_palette_schema_and_hex(candidate_palette):
                    result_json = temp_result
                    palette = candidate_palette
                    break
            except Exception as retry_err:
                print(f"[AI Synthesis Attempt {attempt + 1} Failed]: {retry_err}")
                traceback.print_exc()
                continue

    if not result_json or not palette:
        return None
                
    # KS A 0011 한국어 색이름으로 표시 (저장된 예전 결과도 같은 HEX → 같은 이름. 영문은 color_name_en)
    palette = apply_ks_names(palette)
    result_json["palette"] = palette
    memory_summary = result_json.get("memory_summary") or memory

    # 면적비 가중치 기반 고유 암호 해시(MaC-L.C.H) 산출
    specimen_hash = compute_weighted_lch_hash(palette)
    result_json["specimen_hash"] = specimen_hash
    result_json["specimen_code"] = specimen_hash

    # [장기 기억] 새로 분석한 문장은 저장 (실패해도 사용자 응답에는 영향 없음, 이메일 주소는 저장하지 않음)
    if save and not from_cache:
        try:
            save_result(memory, result_json, palette, memory_summary, specimen_hash, pipeline_version)
        except Exception as save_err:
            print(f"[Memory Save Warning]: {save_err}")

    return {
        "result_json": result_json,
        "palette": palette,
        "memory_summary": memory_summary,
        "specimen_hash": specimen_hash,
        "from_cache": from_cache,
        "painting_base64": painting_b64,      # v2: 새로 그린 경우만 (저장된 문장 재사용 시 None — 그림 저장소 연결 전)
        "paint_error": paint_error,
    }


# ------------------------------------------------------------------------------
# 4. 핵심 비즈니스 로직 (요청 처리 → 응답 payload 생성까지)
# ------------------------------------------------------------------------------
def handle_concierge_inquiry(data: dict):
    """컨시어지 문의 폼 접수 처리 (AI 추론 없이 Resend로 바로 발송)"""
    client_name = str(data.get("client_name", "") or "익명").strip()
    client_contact = str(data.get("client_contact", "")).strip()
    subject_type = str(data.get("subject", "일반 문의")).strip()
    message = str(data.get("message", "")).strip()

    # 필수 입력값 검증
    if not client_contact or not message:
        return 400, {"status": "error", "error": "회신 연락처와 문의 내용을 입력해 주세요."}
    if len(client_name) > 60 or len(client_contact) > 120 or len(subject_type) > 60 or len(message) > 3000:
        return 400, {"status": "error", "error": "입력이 너무 깁니다. 이름 60자, 연락처 120자, 문의 내용 3000자 이내로 적어 주세요."}

    # 문의 유형 텍스트 매핑 가독성 처리
    subject_map = {
        "artwork": "Acquisition of Curated Artwork (전시작 원화 소장)",
        "bespoke": "Bespoke Memory Color Commission (1:1 맞춤 조색 의뢰)",
        "partnership": "Confidential Partnership & Curation (비공개 협업 및 전시 대관)",
        "viewing": "Private Salon Viewing (프라이빗 뷰잉 세션 예약)"
    }
    subject_title = subject_map.get(subject_type, subject_type)

    # 메일 HTML 에 넣을 값은 이스케이프 (입력에 HTML·링크를 넣어 관리자 메일을 꾸미는 것 방지)
    import html as _html
    safe_name, safe_contact = _html.escape(client_name), _html.escape(client_contact)
    safe_subject, safe_message = _html.escape(subject_title), _html.escape(message)

    # 네이버웍스 메일함으로 전달될 우아한 미니멀 HTML 서식 생성
    inquiry_html = f"""
    <div style="max-width: 620px; margin: 20px auto; font-family: -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Pretendard', sans-serif; color: #2b2725; line-height: 1.7; border: 1px solid #eae6e1; border-radius: 12px; overflow: hidden; background: #ffffff;">
      <div style="background: #3a3430; padding: 24px 32px; color: #f7f6f4;">
        <p style="margin: 0; font-size: 11px; letter-spacing: 0.25em; text-transform: uppercase; color: #c4baa9;">MaC Architecture of Memory &amp; Color</p>
        <h2 style="margin: 6px 0 0 0; font-size: 20px; font-weight: 700; letter-spacing: -0.01em; color: #ffffff;">프라이빗 컨시어지 문의가 접수되었습니다</h2>
      </div>
      <div style="padding: 32px;">
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 24px;">
          <tr style="border-bottom: 1px solid #f0ede8;">
            <td style="padding: 12px 0; width: 120px; font-size: 12.5px; font-weight: 700; color: #8c827a; letter-spacing: 0.1em; text-transform: uppercase;">CLIENT NAME</td>
            <td style="padding: 12px 0; font-size: 15px; font-weight: 600; color: #1c1917;">{safe_name}</td>
          </tr>
          <tr style="border-bottom: 1px solid #f0ede8;">
            <td style="padding: 12px 0; font-size: 12.5px; font-weight: 700; color: #8c827a; letter-spacing: 0.1em; text-transform: uppercase;">CONTACT INFO</td>
            <td style="padding: 12px 0; font-size: 15px; font-weight: 600; color: #8c7042;">{safe_contact}</td>
          </tr>
          <tr style="border-bottom: 1px solid #f0ede8;">
            <td style="padding: 12px 0; font-size: 12.5px; font-weight: 700; color: #8c827a; letter-spacing: 0.1em; text-transform: uppercase;">REQUEST TYPE</td>
            <td style="padding: 12px 0; font-size: 14px; font-weight: 600; color: #383330;">{safe_subject}</td>
          </tr>
        </table>

        <p style="font-size: 12px; font-weight: 700; color: #8c827a; letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 8px;">PRIVATE NOTE</p>
        <div style="background: #faf8f5; border-left: 3.5px solid #8c7042; padding: 18px 20px; border-radius: 4px; font-size: 14.5px; color: #383330; line-height: 1.8; white-space: pre-wrap;">{safe_message}</div>

        <div style="margin-top: 36px; padding-top: 20px; border-top: 1px solid #eae6e1; font-size: 11.5px; color: #a39c94; text-align: center;">
          본 메일은 MaC 웹사이트 Color Concierge 데스크에서 실시간 자동 발송되었습니다.
        </div>
      </div>
    </div>
    """

    # 관리자 알림 메일: 받는 주소는 환경변수 CONCIERGE_NOTIFY_EMAIL (쉼표로 여러 개). 없으면 메일 없이 DB 에만 저장.
    # (없는 주소로 보내면 반송이 쌓여 mac.ai.kr 발송 평판이 떨어지고, 사용자 표본 메일까지 스팸함으로 갈 수 있음)
    notify_to = [a.strip() for a in os.getenv("CONCIERGE_NOTIFY_EMAIL", "").split(",") if EMAIL_REGEX.match(a.strip())]
    email_sent = False
    try:
        if RESEND_API_KEY and notify_to:
            resend.Emails.send({
                "from": "MaC Concierge <curator@mac.ai.kr>",
                "to": notify_to,
                "subject": f"[MaC Desk] {client_name[:30]}님의 프라이빗 문의",
                "html": inquiry_html,
            })
            email_sent = True
    except Exception as send_err:
        print(f"[Concierge Mail Send Error]: {send_err}")

    # Supabase concierge_inquiries 에도 기록 (메일이 실패해도 문의가 남도록)
    saved = save_inquiry(client_name, client_contact, subject_type, message, email_sent)

    if email_sent or saved:
        return 200, {"status": "success", "message": "문의가 성공적으로 전달되었습니다."}
    return 500, {"status": "error", "error": "문의 메일 전송 중 통신 오류가 발생했습니다."}


def handle_hex_finder(data: dict):
    """
    챗봇 '추억의 HEX 찾기' 처리.
    - 이메일 없이 기억 문장만으로 4색 팔레트를 반환합니다. 해시는 포함하지 않습니다. (메일 발송 없음)
    - 기억 문장 키는 memory / message / text 중 먼저 값이 있는 것을 사용합니다.
    """
    memory = ""
    for key in ("memory", "message", "text"):
        value = normalize_memory(data.get(key, ""))
        if value:
            memory = value
            break

    err = validate_memory_text(memory)
    if err:
        return 400, {"status": "error", "error": err}

    analysis = run_memory_analysis(memory, save=False)  # 가볍게 보여주기만: DB 저장 없음
    if not analysis:
        return 502, {
            "status": "error",
            "error": "색채 표본 추출에 실패했습니다. 문장을 조금 더 구체적으로 작성해 주세요.",
        }

    palette = analysis["palette"]
    return 200, {
        "status": "success",
        "message": "추억의 HEX를 찾았어요.",
        # 해시(코드)는 응답에 포함하지 않습니다. 사용자가 요청했을 때만 hex_hash 분기로 계산해 전달합니다.
        "palette": palette,
        "hex_codes": [chip.get("hex") for chip in palette],
        "memory_summary": analysis["memory_summary"],
        "cached": analysis["from_cache"],
    }


def handle_hex_hash(data: dict):
    """
    사용자가 '해시/코드'를 요청했을 때만 호출되는 분기.
    - 클라이언트가 보관 중인 4색 팔레트(hex + weight/area_ratio)를 받아 해시를 계산해 돌려줍니다.
    - AI 호출과 DB 접근이 없는 순수 계산입니다.
    """
    palette_in = data.get("palette")
    if not isinstance(palette_in, list) or len(palette_in) != 4:
        return 400, {"status": "error", "error": "팔레트 정보가 올바르지 않아요."}

    clean = []
    for chip in palette_in:
        if not isinstance(chip, dict):
            return 400, {"status": "error", "error": "팔레트 정보가 올바르지 않아요."}
        item = {"hex": str(chip.get("hex", "")).strip().upper()}
        # compute_weighted_lch_hash와 같은 우선순위: weight → area_ratio
        for key in ("weight", "area_ratio"):
            if key in chip:
                try:
                    item[key] = float(chip[key])
                except (TypeError, ValueError):
                    return 400, {"status": "error", "error": "팔레트 정보가 올바르지 않아요."}
                break
        clean.append(item)

    if not validate_palette_schema_and_hex(clean):
        return 400, {"status": "error", "error": "팔레트 정보가 올바르지 않아요."}

    return 200, {"status": "success", "hash": compute_weighted_lch_hash(clean)}


def render_email_html(memory: str, memory_summary: str, specimen_hash: str, palette: list) -> str:
    """emailTemplate.html 바인딩"""
    template_path = Path(__file__).resolve().parent.parent / "emailTemplate.html"
    rendered_html = ""
    if not template_path.exists():
        return rendered_html

    try:
        with open(template_path, "r", encoding="utf-8") as f:
            rendered_html = f.read()

        rendered_html = rendered_html.replace("{{ specimen_hash }}", specimen_hash)
        rendered_html = rendered_html.replace("{{specimen_hash}}", specimen_hash)
        rendered_html = rendered_html.replace("{{SPECIMEN_HASH}}", specimen_hash)

        rendered_html = rendered_html.replace("{{ memory_summary }}", memory_summary)
        rendered_html = rendered_html.replace("{{memory_summary}}", memory_summary)
        rendered_html = rendered_html.replace("{{MEMORY_SUMMARY}}", memory_summary)
        rendered_html = rendered_html.replace("{{USER_MEMORY}}", memory)

        for i in range(4):
            chip = palette[i] if i < len(palette) else {}
            c_name = chip.get("color_name", f"Color {i + 1}")
            c_hex = chip.get("hex", "#CCCCCC").upper()

            rendered_html = rendered_html.replace(f"{{{{ palette[{i}].color_name }}}}", c_name)
            rendered_html = rendered_html.replace(f"{{{{palette[{i}].color_name}}}}", c_name)
            rendered_html = rendered_html.replace(f"{{{{ palette[{i}].hex }}}}", c_hex)
            rendered_html = rendered_html.replace(f"{{{{palette[{i}].hex}}}}", c_hex)
    except Exception as t_err:
        print(f"[Template Render Warning]: {t_err}")

    return rendered_html


def process_request(data: dict):
    """
    요청 데이터(dict)를 받아 (status_code, response_dict) 튜플을 반환합니다.
    """
    request_type = data.get("type")

    # [분기 1] 컨시어지 문의 폼 접수
    if request_type == "concierge_inquiry":
        return handle_concierge_inquiry(data)

    # [분기 2] 챗봇 '추억의 HEX 찾기'
    if request_type in ("hex_finder", "chat_hex"):
        return handle_hex_finder(data)

    # [분기 3] 챗봇에서 사용자가 해시(코드)를 요청했을 때
    if request_type == "hex_hash":
        return handle_hex_hash(data)

    # [기본 로직] 색채 표본 분석 및 발송 처리
    memory = normalize_memory(data.get("memory", ""))
    email = str(data.get("email", "")).strip()

    # [예외 기준 1] 입력 유효성 검증 실패 (HTTP 400 Bad Request)
    err = validate_memory_text(memory)
    if err:
        return 400, {"error": err}

    if not email or not EMAIL_REGEX.match(email):
        return 400, {"error": "올바른 이메일 주소 형식을 입력해 주세요."}

    analysis = run_memory_analysis(memory)
    if not analysis:
        return 502, {"error": "색채 표본 추출에 실패했습니다. 문장을 조금 더 구체적으로 작성해 주세요."}

    result_json = analysis["result_json"]
    palette = analysis["palette"]
    memory_summary = analysis["memory_summary"]
    specimen_hash = analysis["specimen_hash"]
    from_cache = analysis["from_cache"]

    rendered_html = render_email_html(memory, memory_summary, specimen_hash, palette)

    # [예외 기준 4] Resend 트랜잭션 메일 발송 처리 (Non-blocking)
    email_sent = True
    email_error_log = None

    if RESEND_API_KEY and rendered_html:
        try:
            params = {
                "from": "MaC <curator@mac.ai.kr>",
                "to": [email],
                "subject": f"MaC Chromatic Specimen · 당신의 기억이 색이 되었어요",
                "html": rendered_html,
            }
            resend.Emails.send(params)
        except Exception as mail_err:
            email_sent = False
            email_error_log = str(mail_err)
            print(f"[Resend Non-blocking Error]: {email_error_log}")
    else:
        email_sent = False

    response_payload = {
        "status": "success",
        "message": "색채 표본이 성공적으로 추출되었습니다.",
        "hash": specimen_hash,
        "specimen_code": specimen_hash,
        "palette": palette,
        "memory_summary": memory_summary,
        "email_sent": email_sent,
        "email_error": email_error_log,
        "cached": from_cache,
        "data": result_json,
        # v2: 색면추상화 그림 (data URL). 그림이 실패했거나 저장된 문장 재사용이면 None
        "painting": f"data:image/jpeg;base64,{analysis['painting_base64']}" if analysis.get("painting_base64") else None,
        "paint_error": analysis.get("paint_error"),
    }

    return 200, response_payload


# ------------------------------------------------------------------------------
# 5. Vercel Serverless 진입점 (BaseHTTPRequestHandler)
# ------------------------------------------------------------------------------
class handler(BaseHTTPRequestHandler):

    def _send_json(self, status_code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        # 브라우저 사전 요청 (CORS 프리플라이트) 처리
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        self._send_json(405, {"error": "Method not allowed"})

    def do_POST(self):
        # JSON 본문(Body) 데이터 수신 및 파싱
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
        except Exception:
            self._send_json(400, {"error": "유효하지 않은 JSON 데이터입니다."})
            return

        try:
            status_code, payload = process_request(data)
        except Exception as unexpected_err:
            print(f"[Unhandled Error]: {unexpected_err}")
            traceback.print_exc()
            self._send_json(500, {"error": "서버 내부 오류가 발생했습니다."})
            return

        self._send_json(status_code, payload)
