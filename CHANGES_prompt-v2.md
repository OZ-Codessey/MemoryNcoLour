# CHANGES prompt-v2 — 색 기준표·프롬프트 v2 (그림 → 칩 방식)

## 전제가 바뀜
- v1: LLM이 4색을 고정 면적비(45/30/15/10)로 바로 뽑아 칩으로 보여 줌
- v2: LLM이 그림용 팔레트 6~10색을 계획 → Gemini가 그 팔레트로 그림 → 그림에서 면적 상위 4색을 측정해 칩으로 만듦

## 파일 (기존 v1 파일은 그대로 둠)
| 파일 | 내용 |
|---|---|
| `prompts/color_association_v2.py` | 기준표. 역할별 색 묶음(주조 1~2 · 보조 2~3 · 분위기 2~3 · 강조 1~2, 목표 면적 0.45/0.30/0.15/0.10), 정서 13종(부정·복합 포함), 기억 질감 4종, 시간·계절·공간 예시, 감각 규칙, 재료·붓질 규칙 |
| `prompts/color_prompt_v2.py` | 텍스트 LLM 프롬프트, 응답 스키마(`RESPONSE_SCHEMA`), 검증·정규화(`validate_and_normalize`) |
| `prompts/paint_prompt.py` | 그림 지시문 조립. 원문·영문 색이름은 넘기지 않고 HEX + 색 묘사 + 면적만 |
| `prompts/paint_style_system.txt` | 그림 고정 스타일(사물 없음 · 지정 색만 · 스푸마토 · 붓질 · 레퍼런스는 질감만) |
| `tests/test_prompts_v2.py` | 오프라인 시험 9개 (`python tests/test_prompts_v2.py`) |

## v1에서 유지한 것
- 4역할 위계와 목표 면적, `COLOR_MAPPING_PRIORITY` 값, 대원칙의 핵심

## 응답 형식 (LLM → 서버)
```json
{
  "memory_summary": "…", "dominant_emotion": "calm_peace", "texture": "소중한",
  "palette": [{"role": "dominant", "color_name": "Ember Dusk", "hex": "#D96B43", "share": 0.28, "reason": "…"}],
  "painting": {"medium": "oil", "energy": "calm", "mood_keywords": ["serene", "golden", "unhurried"]}
}
```

## 연결할 때 바꿀 곳 (아직 안 함)
- `api/analyze.py`: `validate_palette_schema_and_hex`(정확히 4개) → `validate_and_normalize`
- `services/llm_engine.py`: config에 `response_json_schema: RESPONSE_SCHEMA` 추가(지원 모델일 때)
- 칩·해시·DB 저장은 그림 측정 단계가 생긴 뒤 측정한 4색 기준으로
