-- =============================================================================
-- 컨시어지 문의 기록 — Supabase SQL Editor 에서 한 번 실행 (다시 실행해도 안전)
-- =============================================================================
-- 사이트 '문의하기'(MaC Concierge) 폼으로 들어온 문의를 저장한다. 관리자 메일(Concierge@mac.ai.kr)은 그대로 보내고,
-- 메일이 실패해도 문의는 여기 남는다. 연락처가 들어 있으므로 서버(service role 키)만 읽고 쓸 수 있다.
-- (backend_ref/sql/schema.sql 의 concierge_requests 와 이름이 겹치지 않게 concierge_inquiries 로 둔다)

create table if not exists public.concierge_inquiries (
  id           uuid primary key default gen_random_uuid(),
  client_name  text,
  contact      text        not null,                 -- 회신 이메일 또는 연락처 (개인정보)
  request_type text,                                 -- artwork / bespoke / partnership / viewing
  message      text        not null,
  status       text        not null default 'new'
               check (status in ('new', 'in_progress', 'done', 'spam')),
  email_sent   boolean     not null default false,   -- 관리자 메일 발송 성공 여부
  created_at   timestamptz not null default now()
);
create index if not exists concierge_inquiries_created on public.concierge_inquiries (created_at desc);
alter table public.concierge_inquiries enable row level security;   -- 정책 없음 = 브라우저(anon) 접근 불가
grant select, insert, update, delete on public.concierge_inquiries to service_role;

-- 확인:   select created_at, client_name, request_type, status, email_sent from concierge_inquiries order by created_at desc;
-- 처리 표시: update concierge_inquiries set status = 'done' where id = '…';
