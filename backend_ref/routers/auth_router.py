"""
auth_router.py — 회원 인증 라우터 (prefix: /auth)

[변경 요약 — 네이버는 되는데 지메일 가입이 안 되는 문제 점검/개선]
1) 이메일 정규화(공백 제거 + 소문자) — 대소문자/공백 차이로 인한 중복·불일치 방지
2) Supabase 오류를 "원인별"로 구분해 응답
   - 인증 메일 발송 한도 초과(over_email_send_rate_limit) → 429
   - 허용되지 않은 이메일/도메인(email_address_invalid) → 400
   - 이미 가입된 이메일 → 400
   - 가입 비활성화(signup_disabled) → 403
3) '이미 가입된 이메일'인데 Supabase가 에러 없이 가짜 user를 돌려주는 경우
   (이메일 확인 ON일 때 user.identities 가 빈 배열) 감지 → 400
   (기존 코드는 이 가짜 id로 profiles upsert → FK 오류 → 500 으로 보였음)
4) profiles 생성 실패 시 방금 만든 Auth 유저 롤백(고아 계정 방지)
5) 응답에 email_confirmation_required 포함 (메일 인증 대기 상태 구분)
6) 로그인: 이메일 미인증(email_not_confirmed)을 별도 안내 (기존엔 전부 '비밀번호 확인'으로 보였음)
7) 실제 예외 원인을 서버 로그에 항상 남김(logger.exception)

엔드포인트:
  POST /auth/signup   회원가입 + profiles 생성
  POST /auth/login    로그인 → access_token + role 반환
  GET  /auth/me       내 정보 (Bearer 토큰 필요)
  PATCH /auth/me      내 정보 수정
  DELETE /auth/me     회원 탈퇴

⚠️ profiles 테이블은 sql/schema.sql 기준 (id, email, name, role, credits, free_credit_claimed, updated_at).
가입 직후 크레딧은 0 — 무료 크레딧은 추가 인증 후 POST /api/me/credits/claim 으로만 지급(Q1).
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator

from utils.auth import get_current_user_id
from utils.supabase_client import supabase, supabase_admin
from utils.limiter import limiter


def _require_sb():
    if supabase is None:
        raise HTTPException(status_code=503, detail="인증 서버(Supabase)가 설정되지 않았습니다.")


logger = logging.getLogger("uvicorn")

router = APIRouter(prefix="/auth", tags=["Auth"])


# ── Helpers ───────────────────────────────────────────────────────────────
def _err_info(e: Exception) -> tuple[str, str, int | None]:
    """Supabase(AuthApiError 등) 예외에서 (code, message, status) 추출."""
    code = str(getattr(e, "code", "") or "")
    msg = str(getattr(e, "message", "") or e)
    status = getattr(e, "status", None)
    return code, msg, status if isinstance(status, int) else None


def _mask_email(email: str) -> str:
    """로그용 이메일 마스킹 (a***@gmail.com)."""
    try:
        local, domain = email.split("@", 1)
        return f"{local[:1]}***@{domain}"
    except Exception:
        return "***"


# ── Schemas ───────────────────────────────────────────────────────────────
class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, description="비밀번호 (6자 이상)")
    name: str = Field(default="이름없음", description="사용자 이름")

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v):
        return v.strip().lower() if isinstance(v, str) else v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v):
        return v.strip().lower() if isinstance(v, str) else v


class ProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=30)


# ── 회원가입 ────────────────────────────────────────────────────────────────
@router.post("/signup", summary="회원가입 + 기본 role 생성")
@limiter.limit("5/hour")
def signup(request: Request, req: SignupRequest):
    _require_sb()
    logger.info(f"🆕 회원가입 요청: {_mask_email(req.email)}")

    user_id = None
    try:
        auth_res = supabase.auth.sign_up(
            {
                "email": req.email,
                "password": req.password,
                "options": {"data": {"name": req.name}},
            }
        )
        if not auth_res.user:
            raise HTTPException(status_code=400, detail="회원가입 실패 (Auth User 생성 불가)")

        # 이메일 확인이 켜져 있으면, 이미 가입된 이메일도 에러 없이 '가짜 user'가 온다.
        # 이때 identities 는 빈 배열 → 중복 가입으로 처리.
        identities = getattr(auth_res.user, "identities", None)
        if identities is not None and len(identities) == 0:
            logger.warning(f"⚠️ 이미 가입된 이메일(identities 비어있음): {_mask_email(req.email)}")
            raise HTTPException(status_code=400, detail="이미 가입된 이메일입니다.")

        user_id = auth_res.user.id

        try:
            supabase_admin.table("profiles").upsert(
                {
                    "id": user_id,
                    "email": req.email,
                    "name": req.name,
                    "role": "user",
                    "credits": 0,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).execute()
        except Exception:
            # profiles 생성 실패 → 고아 Auth 계정이 남지 않도록 롤백
            logger.exception(f"❌ profiles 생성 실패, Auth 유저 롤백 시도: {user_id}")
            try:
                supabase_admin.auth.admin.delete_user(user_id)
            except Exception as rb_err:
                logger.error(f"🚨 Auth 롤백 실패({user_id}): {rb_err}")
            raise HTTPException(status_code=500, detail="회원가입 처리 중 오류가 발생했습니다.")

        needs_confirm = auth_res.session is None  # 이메일 확인 ON이면 세션이 없음
        logger.info(f"✅ 회원가입 완료: {user_id} (confirm_required={needs_confirm})")
        return {
            "status": "ok",
            "user_id": user_id,
            "role": "user",
            "email_confirmation_required": needs_confirm,
        }

    except HTTPException:
        raise
    except Exception as e:
        code, msg, status = _err_info(e)
        low = f"{code} {msg}".lower()
        # 진짜 원인을 반드시 로그에 남김 (사용자에게는 노출하지 않음)
        logger.exception(f"❌ 회원가입 오류 [code={code!r} status={status}]: {msg}")

        if status == 429 or "rate limit" in low or code.startswith("over_"):
            raise HTTPException(
                status_code=429,
                detail="인증 메일 발송 한도를 초과했습니다. 잠시 후 다시 시도해주세요.",
            )
        if "already" in low and ("registered" in low or "exists" in low):
            raise HTTPException(status_code=400, detail="이미 가입된 이메일입니다.")
        if code == "email_address_invalid" or ("email" in low and "invalid" in low):
            raise HTTPException(status_code=400, detail="사용할 수 없는 이메일 주소입니다.")
        if code == "signup_disabled" or ("signup" in low and "disabled" in low):
            raise HTTPException(status_code=403, detail="현재 회원가입이 비활성화되어 있습니다.")
        if code == "weak_password" or "password" in low:
            raise HTTPException(status_code=400, detail="비밀번호 조건을 확인해주세요.")
        raise HTTPException(status_code=500, detail="회원가입 처리 중 오류가 발생했습니다.")


# ── 로그인 ──────────────────────────────────────────────────────────────────
@router.post("/login", summary="로그인 + role 반환")
@limiter.limit("10/minute")
def login(request: Request, req: LoginRequest):
    _require_sb()
    try:
        auth = supabase.auth.sign_in_with_password(
            {"email": req.email, "password": req.password}
        )
        if not auth.session:
            raise HTTPException(status_code=401, detail="이메일 또는 비밀번호를 확인해주세요.")

        user_id = auth.user.id
        user_role, user_name = "user", ""
        try:
            profile = (
                supabase_admin.table("profiles")
                .select("role,name")
                .eq("id", user_id)
                .maybe_single()
                .execute()
            )
            if profile and profile.data:
                user_role = profile.data.get("role", "user")
                user_name = profile.data.get("name", "")
        except Exception as pe:
            logger.error(f"🚨 role 조회 에러(로그인은 허용): {pe}")

        logger.info(f"🔑 로그인 성공: {user_id} (role={user_role})")
        return {
            "access_token": auth.session.access_token,
            "refresh_token": auth.session.refresh_token,
            "user_id": user_id,
            "email": auth.user.email,
            "name": user_name,
            "role": user_role,
        }
    except HTTPException:
        raise
    except Exception as e:
        code, msg, status = _err_info(e)
        low = f"{code} {msg}".lower()
        logger.error(f"❌ 로그인 오류 [code={code!r} status={status}]: {msg}")

        if code == "email_not_confirmed" or "not confirmed" in low:
            raise HTTPException(
                status_code=403,
                detail="이메일 인증이 완료되지 않았습니다. 메일함(스팸함 포함)에서 인증 링크를 눌러주세요.",
            )
        if status == 429 or "rate limit" in low:
            raise HTTPException(status_code=429, detail="요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")
        raise HTTPException(status_code=400, detail="이메일 또는 비밀번호를 확인해주세요.")


# ── 내 정보 ────────────────────────────────────────────────────────────────
@router.get("/me", summary="내 정보 조회")
def get_my_profile(user_id: str = Depends(get_current_user_id)):
    _require_sb()
    try:
        res = (
            supabase_admin.table("profiles")
            .select("id,email,name,role,credits,free_credit_claimed,updated_at")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
        if not res or not res.data:
            raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")
        return res.data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 내 정보 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="프로필 조회 실패")


@router.patch("/me", summary="내 정보 수정")
def patch_my_profile(
    req: ProfileUpdateRequest,
    user_id: str = Depends(get_current_user_id),
):
    _require_sb()
    payload = {k: v for k, v in req.model_dump().items() if v is not None}
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            supabase_admin.table("profiles").update(payload).eq("id", user_id).execute()
        )
        return {"status": "ok", "updated": len(res.data or []), "payload": payload}
    except Exception as e:
        logger.error(f"❌ 프로필 수정 실패: {e}")
        raise HTTPException(status_code=500, detail="프로필 수정 실패")


@router.delete("/me", summary="회원 탈퇴")
def delete_my_profile(user_id: str = Depends(get_current_user_id)):
    """탈퇴 시 표본·원문·로그도 함께 삭제 (FK on delete cascade, sql/schema.sql)."""
    _require_sb()
    try:
        supabase_admin.table("profiles").delete().eq("id", user_id).execute()
        try:
            supabase_admin.auth.admin.delete_user(user_id)
        except Exception as auth_err:
            logger.warning(f"⚠️ Auth 삭제 경고({user_id}): {auth_err}")
        return {"status": "ok", "deleted": True, "user_id": user_id}
    except Exception as e:
        logger.error(f"❌ 탈퇴 처리 실패: {e}")
        raise HTTPException(status_code=500, detail="탈퇴 처리 실패")