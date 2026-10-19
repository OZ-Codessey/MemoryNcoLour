# MaC v2 통합 안내 — 기억 문장 → 색면추상화 → 칩 4개

## 무엇이 바뀌나
| 전 (legacy) | 후 (v2, 기본) |
|---|---|
| Gemini 텍스트가 4색 HEX 를 바로 지어냄 | Claude(Codyssey 키)가 팔레트 4~8색 계획 → Gemini 이미지가 색면추상화 → **그림에서 면적 큰 4색**을 칩으로 |
| 결과 모달: 칩 4개 | 결과 모달: **그림** + 칩 4개 (비율 = 그림에서 잰 실제 면적) |
| 응답 약 5초 | 응답 약 30~50초 (팔레트 15초 + 그림 20초 안팎) |

`MAC_ENGINE=legacy` 환경변수를 넣으면 예전 방식으로 바로 되돌아간다.

## 영향 범위
- 바뀜: `api/analyze.py` 의 **기억 분석 부분**, `index.html` 결과 모달(그림 자리 + 버튼 문구), `vercel.json`(analyze 함수 설정 추가), `requirements.txt`(pillow·numpy), `.gitignore`(out/)
- 그대로: 컨시어지 문의·챗봇(`api/chat.py`)·사주(`api/astral*`, `astral/`) 코드
- 챗봇 '추억의 HEX 찾기'(hex_finder)는 빠르게 답해야 해서 **그림 없이** 팔레트 상위 4색만 돌려준다. 응답 형식은 그대로.

## 파일
| 파일 | 설명 |
|---|---|
| `api/analyze.py` | `MAC_ENGINE=v2`(기본) 이면 `services/mac_pipeline.run_v2` 호출. 응답에 `painting`(data URL)·`paint_error` 추가 |
| `index.html` | 결과 모달에 그림(`#paintingBox`). 그림이 없으면 숨기고, 혼잡으로 실패하면 안내 한 줄 |
| `services/mac_pipeline.py` | 팔레트 → 그림 → 칩. 그림 실패 시 계획 팔레트 상위 4색으로 칩을 대신 채움 |
| `services/claude_client.py` | Codyssey Claude 호출 (requests, 재시도, 구조화 출력 자동 대체) |
| `services/painter.py` | Gemini 이미지 REST 호출. 레퍼런스 3장 + 직전 그림 첨부, 503 이면 재시도 → 다른 모델로 자동 전환 |
| `services/measure.py` | 그림 → 칩 4개 (numpy k-means, 같은 색 계열만 합침, 영문 이름 항상 붙음) |
| `services/card.py` | 결과 카드 HTML (로컬 시험용) |
| `prompts/color_association_v2.py`, `color_prompt_v2.py` | 팔레트 기준표·프롬프트 v2.4 (기존 v1 파일은 그대로 둠) |
| `prompts/paint_prompt.py`, `paint_style_system.txt` | 그림 지시문 |
| `references/ref_01~03.jpg` | 그림 화풍 레퍼런스 (1024px). ※ ref_02 는 스톡 워터마크가 있어 서비스 전 교체 권장 |
| `scripts/` | 로컬 시험 도구 (`local_site.py`, `try_paint.py`, `make_card.py`, 진단 스크립트) |
| `tests/test_prompts_v2.py` | 오프라인 시험 10개 |

## 환경변수 (Vercel → Settings → Environment Variables)
| 이름 | 필요 | 값 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 필수 (챗봇과 공용) | Codyssey 키 `sk-cody-…` |
| `ANTHROPIC_BASE_URL` | 필수 | `https://copa.codyssey.kr` |
| `MAC_CLAUDE_MODEL` | 선택 | 기본 `claude-sonnet-4` |
| `GEMINI_API_KEY` | 필수 | 결제된(Tier 1) Gemini 키 |
| `GEMINI_IMAGE_MODEL` | 선택 | 기본 `gemini-nano-banana-2.1` |
| `GEMINI_IMAGE_FALLBACKS` | 선택 | 기본 `gemini-3.1-flash-image` (혼잡할 때 넘어갈 모델) |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | 결과 저장·재사용·챗봇 기억 | ※ 이름이 `SUPABASE_SERVICE_ROLE_KEY` 여야 함 (`SUPABASE_SECRET_KEY` 만 있으면 저장이 조용히 꺼짐) |
| `RESEND_API_KEY` | 메일 | 기존 그대로 |
| `MAC_ENGINE` | 선택 | `v2`(기본) / `legacy` |

## 로컬 시험
```bash
pip install -r requirements.txt
python tests/test_prompts_v2.py                 # 10개 통과
python scripts/local_site.py                    # http://localhost:8000 → 문장 입력 → Synthesize Palette
```
`local_site.py` 는 `/api/analyze` 만 제공한다 (챗봇·사주는 `vercel dev` 또는 배포 후 시험).
`.env` 에 RESEND·SUPABASE 키를 빼 두면 메일 발송·DB 저장 없이 시험할 수 있다.

## 배포 순서
1. GitHub 에서 최신 main 을 받은 뒤(`git pull`) 이 변경을 적용 (`git apply mac_v2.patch` 또는 파일 덮어쓰기)
2. 로컬 시험 통과 확인 → 커밋·푸시
3. Vercel 환경변수 추가 (위 표) → Redeploy
4. 배포 후 문장 1개로 결과 모달에 그림이 나오는지, 챗봇 HEX 찾기가 그대로 되는지 확인

## 알려진 한계 (다음 작업)
- **그림 저장 없음**: 같은 문장 재사용(캐시) 시 칩·요약은 나오지만 그림은 없다 → Supabase Storage 에 저장하고 `image_path` 에 기록하는 작업 필요. 메일에도 아직 그림이 없다.
- **'직전 그림' 첨부는 같은 서버 인스턴스 안에서만** 기억한다 (그림 저장소를 붙이면 거기서 가져오도록 바꿀 자리).
- 팔레트가 밝은 파스텔에 치우치는 경우가 있다 (명도 폭 검사·재요청 — 백로그).
- 응답 30~50초 동안 버튼에 'Painting your memory…' 만 표시된다 (진행 표시 개선 여지).
