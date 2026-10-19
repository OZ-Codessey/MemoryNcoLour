-- MaC — mac_results 테이블 하나만 (Supabase SQL Editor 에 붙여넣고 Run). 다시 실행해도 안전.
create table if not exists public.mac_results (
  sentence_key     text primary key check (sentence_key ~ '^[0-9a-f]{64}$'),  -- 문장 고유 해시
  sentence         text,                                                    -- 입력 문장
  memory_summary   text,                                                    -- 한 줄 요약
  chip1_hex        text check (chip1_hex ~ '^#[0-9A-F]{6}$'),               -- 주조 45%
  chip2_hex        text check (chip2_hex ~ '^#[0-9A-F]{6}$'),               -- 보조 30%
  chip3_hex        text check (chip3_hex ~ '^#[0-9A-F]{6}$'),               -- 분위기 15%
  chip4_hex        text check (chip4_hex ~ '^#[0-9A-F]{6}$'),               -- 강조 10%
  chip_names       text[],                                                  -- 칩 4개 KS 색이름
  specimen_hash    text not null,
  specimen_code    text,                                                    -- MaC-XXXX-XXXX-XXXX
  payload          jsonb not null,                                          -- 결과 전체
  pipeline_version text,
  versions         jsonb,
  image_path       text,
  hits             integer not null default 1,
  created_at       timestamptz not null default now(),
  last_hit_at      timestamptz not null default now()
);
create or replace function public.mac_result_hit(p_key text) returns void language sql as $$
  update public.mac_results set hits = hits + 1, last_hit_at = now() where sentence_key = p_key;
$$;
revoke execute on function public.mac_result_hit(text) from public, anon, authenticated;
grant  execute on function public.mac_result_hit(text) to service_role;
alter table public.mac_results enable row level security;   -- 서버 키로만 읽고 쓰기
