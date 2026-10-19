# CHANGES ks-1 — KS A 0011 색 엔진 (코퍼스 · 임베딩 RAG · 팔레트)

작성: 태규 (코퍼스·RAG·임베딩 담당) · 2026-10-18
대상 저장소: **Final_Project_MaC-main** (통합본). 경로: 사이트 `index.html → /api/analyze → api/analyze.py` (Vercel).

## 1. 무엇이 바뀌나

예전: Gemini 가 문장을 보고 4색 이름과 HEX 를 **직접 지어냄** → 비KS 이름·웹색 HEX, 같은 문장이어도 결과가 흔들림.
지금: 언어 모델(Claude)은 **문장 나누기와 후보 중 고르기만** 하고, 색은 전부 **KS A 0011 색이름표**에서 나온다.

```
문장 ─①저장소에 같은 문장? → 있으면 그대로 반환 (같은 문장 = 같은 칩)
     ─② Claude 나누기: 7축 단어 + 기억 질감 + 요약 (색은 내지 않음, 구조화 출력으로 JSON 형식 강제)
     ─③ 단어마다: 명시 색('노란 우산') → 코퍼스 정확 일치 → Voyage 임베딩(τ) → Claude 후보 고르기(RAG) → 버림
     ─④ 면적: 팀장님 축 가중치 · 빠진 축 재분배 · 같은 색 합치기 · N<4 면 기억 질감 방향 파생색
     ─⑤ 칩 4개(주조45·보조30·분위기15·강조10) + 그림 팔레트(전체 색·면적) + 그림 지시문 + 표본 코드
```

응답 형식은 예전과 호환된다: `palette`(4개, `color_name`·`hex`·`weight`·`reason`), `hash`, `memory_summary`.
추가 필드: `paint_palette`, `paint_prompt`, `texture`, `data.items`(단어별 매칭 경로).

## 2. 파일

| 파일 | 설명 |
|---|---|
| `api/analyze.py` | 색 추출 부분만 교체. `MAC_ENGINE=legacy` 로 예전 방식 즉시 복귀 가능. import 예비 경로가 진짜 오류를 가리지 않게 고침 (`CHANGES_ks-1.analyze.diff` 참고) |
| `vercel.json` | 기존 rewrites 그대로 + `functions.api/analyze.py.includeFiles` 에 코퍼스·벡터·services·prompts 추가, `maxDuration: 60` |
| `requirements.txt` | 기존 4줄 그대로 + `numpy` 한 줄 추가 (Vercel 이 이 파일로 설치함). Voyage 는 SDK 없이 `requests` 로 부름 — SDK 는 약 90MB 를 더 설치해 함수 크기 한도에 가까워짐 |
| `requirements-dev.txt` | 새 파일: 시험·코퍼스·벡터 재생성용 (`pytest`, `voyageai`, `colour-science`, `openpyxl`) |
| `services/mac_color/` | 엔진. `data`(로딩·버전검사) · `matcher` · `palette` · `prompts` · `keys` · `clients` · `pipeline` |
| `data/kscorpus/ks_colors.json` | KS 색이름표: 쓰는 색 199 + 값 없는 이름 127(대체 이름) |
| `data/kscorpus/words.json` | 단어 코퍼스: 1,888 단어 / 4,527 표기 / 명시 색 표현 526 |
| `data/kscorpus/word_vectors.npz` · `.meta.json` | 표기 임베딩 (voyage-4-large, 1024, "축: 표기") — 태규 PC 에서 실제 API 로 만든 파일 (키 없음, 커밋 가능) |
| `data/kscorpus/words_src/*.tsv` | 단어 원본 (사람이 고치는 곳) · `team_name_map.tsv` 팀장님 규칙 색 이름 → KS 이름 |
| `data/kscorpus/source/ks_table7.tsv`, `ks_table8.tsv` | KS 원문 표 7·8 옮겨 적은 것 |
| `data/kscorpus/source/legacy_surfaces.txt` | 예전 저장소 멘토 키워드·objects 표기 247개 (출처 '기존 자료' 표시용). 그 파일들이 없는 이 저장소에서도 `build_words.py` 결과가 똑같이 나오게 옮겨 둠 |
| `data/kscorpus/corpus_review.xlsx` | 팀 검토용 (색 칸에 실제 색) |
| `scripts/build_ks_colors.py` · `check_ks_colors.py` | 먼셀 → hex·LCh 계산 / 검증 |
| `scripts/build_words.py` · `view_words.py` | 단어 원본 → words.json·xlsx / 확인용 HTML |
| `scripts/build_embeddings.py` | 코퍼스 임베딩 (새 표기만 API) |
| `scripts/compare_embeddings.py` · `analyze_vectors.py` | 임베딩 설정 비교 / 기준값 분석 |
| `scripts/try_sentence.py` | 실제 키로 문장을 넣어 단계별 확인 |
| `tests/test_mac_color.py` | 가짜 API 로 규칙·결정성·Claude 요청 형식·.env 읽기 시험 17개 |
| `.gitignore` · `.env.example` | `.env`·비교용 벡터·시험 결과 제외 |

## 3. 환경변수

| 이름 | 필요 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 필수 | 문장 나누기 (문장당 1회) + 후보 고르기 (고를 것이 있을 때만 1회). **팀 키는 Codyssey 공개 API 의 `sk-cody-…` 키** — Anthropic Messages 규격 그대로라 코드 수정 없이 주소만 바꿔 씀. 요청은 `requests` 로 직접 보냄 (새 패키지 없음) |
| `ANTHROPIC_BASE_URL` | Codyssey 키면 필수 | `https://copa.codyssey.kr` (문서 탭의 'Public API Base URL', 끝에 `/v1` 붙여도 됨). 없으면 Anthropic 공식 주소 |
| `GEMINI_API_KEY` | 선택 | KS 엔진에서는 쓰지 않음. 예전 방식(`MAC_ENGINE=legacy`)이나 `MAC_SPLIT_LLM`/`MAC_PICK_LLM=gemini` 일 때만. (무료 등급 분당 5회 한도로 시연이 막힌 적이 있어 기본에서 뺌) |
| `VOYAGE_API_KEY` | 권장 | 없거나 실패하면 글자 비교 후보로 대체되어 동작은 함(정확도 낮음). **서비스 계정은 결제 수단 등록 권장** — 미등록 계정은 분당 요청 제한이 낮아 시연 때 막힐 수 있음 |
| `MAC_ENGINE` | 선택 | `ks`(기본) / `legacy` |
| `MAC_SPLIT_LLM` · `MAC_PICK_LLM` | 선택 | 나누기·고르기 모델 `claude`(기본)/`gemini` |
| `MAC_CLAUDE_MODEL` | 선택 | Claude 모델 이름. 기본: 주소가 Codyssey 면 `claude-sonnet-4`(문서 탭 예제 모델), 아니면 `claude-sonnet-5-5`. 모델이 구조화 출력·effort 옵션을 받지 않으면 자동으로 빼고 보내며, 통한 방식을 기억해 다음 문장부터는 바로 보냄 |
| `MAC_TAU` `MAC_GAP` `MAC_TOPK` | 선택 | 임베딩 바로 받아들임 기준 0.90 / 2등과 차이 0.02 / 고르기 후보 수 20 |

## 4. 병합 순서

1. 이 묶음을 저장소 맨 위에 푼다 (기존 파일은 `api/analyze.py`, `vercel.json`, `requirements.txt` 만 바뀜, 나머지는 새 파일)
2. 로컬에서 `pip install -r requirements.txt` (시험·재생성까지 하려면 `requirements-dev.txt`)
3. `data/kscorpus/word_vectors.*` 도 묶음에 들어 있다 (태규 PC 에서 실제 API 로 만든 파일). 코퍼스를 고쳐 `build_words.py` 를 다시 돌리면 `build_embeddings.py` 로 벡터도 다시 만들어야 한다 — 버전이 어긋나면 엔진이 시작할 때 오류로 알려 줌
4. `python tests/test_mac_color.py` → 17개 통과 확인
5. `python scripts/try_sentence.py "문장"` 으로 실제 키 확인 (로컬 `.env` — python-dotenv 가 없어도 읽음)
6. Vercel 환경변수에 `ANTHROPIC_BASE_URL`(https://copa.codyssey.kr)·`ANTHROPIC_API_KEY`·`VOYAGE_API_KEY` 추가 후 배포. 배포 후 함수 크기·응답 시간 확인

문제 해결
- `No module named 'llm_engine'`: 예전 묶음에서 `services/llm_engine.py` 안의 `python-dotenv` 가 없을 때 진짜 원인이 가려져 나오던 메시지. 이 묶음에서는 dotenv 없이도 동작하고, 다른 패키지가 빠지면 그 이름과 설치 명령이 그대로 나옴
- `No module named 'numpy'`: `pip install -r requirements.txt`
- `No module named 'voyageai'`: 벡터를 다시 만드는 스크립트에서만 필요 → `pip install -r requirements-dev.txt` (사이트·try_sentence 는 필요 없음)
- Voyage 호출이 실패하면(키 없음·429) 임베딩 대신 글자 비교 후보로 동작 — 결과 `items[].note` 에 '임베딩 실패' 표시

## 5. 숫자로 본 근거

- KS 색: 먼셀 → Munsell Renotation(C) → Bradford → D65 → Lab/LCh, sRGB hex. 화면 밖 37색은 채도만 줄임. 옮겨 적기·역변환·결정성 검사 통과
- 임베딩 설정 6개 비교 (한 개 빼고 찾기, 3,947 표기): voyage-4-large + "축: 표기" 형식이 1등 색 71%(기존 voyage-4 단어만 52%), 상위 20 후보 안 색 97%
- 최종 벡터(코퍼스 정리 후, 3,946 표기)로 다시 잰 값: 1등 색 72%, 상위 20 후보 안 색 97%
- 기준값 τ=0.90·gap 0.02: 약 31% 바로 받아들임, 그중 약 91% 색 정답 (최종 벡터). 나머지는 Claude 가 후보 20개에서 고름

## 6. 확인한 것 / 아직 확인 못 한 것

- 실제 Gemini·Voyage 키로 확인 (2026-10-18): 예시 문장 "비 오는 날 할머니 집 마루에서 노란 우산을 쓰고 귤을 까먹던 따뜻한 오후"
  - Gemini 나누기: 7단어를 팀 규칙대로 축 배정, '노란' 색 표현 분리, 질감 '소중한', 요약 생성 → 7단어 모두 코퍼스 정확 일치
  - Gemini 맥락 색 고르기: 마루→어두운 갈색, 따뜻하다→살구색, 오후→크림색 (모두 후보 안)
  - 칩: 회청색 45 · 귤색 30 · 노랑 15 · 크림색 10, 코드 MaC-E023-6B8E-124B (가짜 API 시험과 같은 코드 = 결정성 확인)
- 위 확인은 **나누기를 Gemini 로 하던 때** 결과. 나누기를 Claude 로 바꾼 뒤에는 가짜 HTTP 시험(요청 형식·재시도·형식 거부 시 단계적 대체·같은 나누기 답 → 같은 코드)만 통과 — **실제 Claude 키로 한 번 확인 필요** (`python scripts/try_sentence.py "문장"`, 첫 줄에 '모델 나누기 claude:…' 표시)
- 실제 Codyssey Claude 키로 나누기 확인 (2026-10-18, claude-sonnet-4): 편백나무 문장이 숲·계곡·아이스크림·추억 등으로 나뉘고 칩 4개 생성
- **실제 키로 아직 못 본 경로**: 코퍼스에 없는 단어의 임베딩 받아들임 / Claude 후보 20개 중 단어 고르기 (위 실행은 Voyage·고르기 키가 빠진 상태였음).
  이 두 경로는 가짜 API 시험과 코퍼스 벡터 오프라인 분석으로만 확인됨.
  다음에 확인할 문장 예: "편백나무 숲길을 걷다 맡은 피톤치드 향과 차가운 계곡물, 형이 사 준 민트초코 아이스크림이 생생하게 떠오른다"
- 수정: 나누기 호출 자체가 실패(503·429)하면 나누기 단계가 다시 부르지 않음 (Claude 클라이언트 안에서 429·5xx 만 한 번 짧게 재시도) (예전엔 llm_engine 재시도 × 2 = 문장당 8회까지 호출해 한도를 바로 넘김)

## 7. 정해지지 않은 것 (지금은 아래 기본값으로 동작)

- 결과 저장소: 지금은 프로세스 메모리(`MemoryStore`) — Vercel 인스턴스가 바뀌면 사라짐. 영구 저장(예: Supabase 테이블 `sentence_key → 결과`)은 팀이 정하면 `get/put` 두 함수만 맞춰 바꿔 끼우면 됨
- 원문 문장은 저장하지 않음 (sentence_key 해시만). 요약문(`memory_summary`)은 결과에 들어 있음
- 코퍼스가 바뀌어도 이미 저장된 문장은 예전 결과 유지
- 기억 질감 '없음'일 때 파생 방향 = 밝은(없으면 흐린) — 팀 규칙에 없어 정한 값
- 대응 관용색 값을 빌려 온 계통색 64개 사용 유지 / 값 없는 계통색 127개는 대체 이름으로
- 팀장님 규칙의 KS 아닌 색 이름 23개 → `team_name_map.tsv` 대응 (팀장님 확인 필요)
- 단어 코퍼스 1,682줄은 검토 전 초안 → `corpus_review.xlsx` 로 검토

## 8. 아직 남은 일

- 실제 문장 평가 세트(7축 라벨)로 τ 최종 확정 (`try_sentence.py --file`)
- 그림 생성 연결: `paint_prompt`·`paint_palette` 를 Gemini 이미지 모델에 넘기는 부분은 사이트 경로에 아직 없음
- pending(버려지거나 고르기 단계가 고른 단어) 로그를 모아 코퍼스 보강
