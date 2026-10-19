-- =============================================================================
-- Astral 전통화 생성 한도 (Vercel 용) — Supabase SQL Editor 에서 한 번 실행
-- =============================================================================
-- Vercel 은 함수 인스턴스마다 디스크가 따로라 SQLite 로는 한도를 지킬 수 없어서, 사용 기록을 여기에 남긴다.
-- 규칙은 기존 astral/art_store.py(SQLite) 와 같다:
--   · 동시에 1장만 생성 (진행 중 예약은 5분 뒤 자동 만료)
--   · 전체 누적 p_total 장, 하루(UTC) p_daily 장
--   · 한 사람(익명 세션) 하루 3장, 같은 그림 요청 3번까지
--   · 외부 이미지 API 를 부르기 '전에' 기록 → 실패·시간초과도 1회로 센다 (비용 보호)
-- 서버(service role 키)만 함수를 부를 수 있고, 브라우저(anon) 는 테이블·함수 모두 접근 불가.

create table if not exists public.astral_art_attempts (
  id           uuid primary key default gen_random_uuid(),
  owner        text        not null,          -- 익명 세션 해시 (개인정보 아님)
  image_key    text        not null,          -- 그림 요청 해시
  started      timestamptz not null default now(),
  active_until timestamptz not null default 'epoch'
);
create index if not exists astral_art_attempts_started on public.astral_art_attempts (started);
create index if not exists astral_art_attempts_owner   on public.astral_art_attempts (owner, started);
alter table public.astral_art_attempts enable row level security;   -- 정책 없음 = anon/authenticated 접근 불가

create or replace function public.astral_art_reserve(
  p_owner text, p_key text, p_daily int, p_total int,
  p_personal int default 3, p_retries int default 3
) returns json
language plpgsql security definer set search_path = public as $$
declare
  v_id  uuid;
  v_day timestamptz := date_trunc('day', now() at time zone 'utc') at time zone 'utc';
begin
  perform pg_advisory_xact_lock(hashtext('astral_art_reserve'));   -- 동시 요청을 한 줄로 세움
  if exists (select 1 from astral_art_attempts where active_until > now()) then
    return json_build_object('error', 'busy');
  end if;
  if (select count(*) from astral_art_attempts) >= p_total
     or (select count(*) from astral_art_attempts where started >= v_day) >= p_daily then
    return json_build_object('error', 'service_limit');
  end if;
  if (select count(*) from astral_art_attempts where owner = p_owner and started >= v_day) >= p_personal
     or (select count(*) from astral_art_attempts where image_key = p_key) >= p_retries then
    return json_build_object('error', 'personal_limit');
  end if;
  insert into astral_art_attempts (owner, image_key, active_until)
  values (p_owner, p_key, now() + interval '300 seconds')
  returning id into v_id;
  return json_build_object('reservation', v_id);
end $$;

create or replace function public.astral_art_finish(p_id uuid) returns void
language sql security definer set search_path = public as $$
  update astral_art_attempts set active_until = 'epoch' where id = p_id;
$$;

revoke all on function public.astral_art_reserve(text, text, int, int, int, int) from public, anon, authenticated;
revoke all on function public.astral_art_finish(uuid) from public, anon, authenticated;
grant execute on function public.astral_art_reserve(text, text, int, int, int, int) to service_role;
grant execute on function public.astral_art_finish(uuid) to service_role;

-- 확인: select * from astral_art_attempts order by started desc limit 20;
-- 누적 한도 초기화(운영자만): delete from astral_art_attempts;
