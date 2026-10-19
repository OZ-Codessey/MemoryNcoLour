"""prompts.py — 언어 모델에게 보내는 지시문 3개 (나누기·고르기 모두 Claude 기본).

1) build_split_prompt  : 문장 → 7축 단어 + 기억 질감 + 한 줄 요약 (색은 내지 않는다)
2) build_pick_prompt   : 코퍼스에서 바로 못 찾은 단어 / 색 후보가 여럿인 단어 → 주어진 후보 중에서만 고르기 (RAG)
3) build_paint_prompt  : 확정된 팔레트(KS 이름·hex·면적) → 그림 지시문

원칙: 언어 모델은 '나누기'와 '후보 중 고르기'만 한다. 색 이름·hex 를 지어내지 않는다.
      모든 색은 KS 색이름표(data/kscorpus/ks_colors.json)에서만 나온다.
"""
from __future__ import annotations

import json

from .data import AXES, Corpus

AXIS_GUIDE = {
    "emotion": "감정·마음·관계의 정서 (예: 설렘, 그리움, 든든함, 가족, 엄마)",
    "memory_color": "눈에 보이는 구체적인 사물·음식·꽃·동물·자연물 (예: 우산, 귤, 벚꽃, 강아지, 눈)",
    "time": "시간대·계절·달·명절·행사날·인생의 시기 (예: 오후, 노을, 겨울, 추석, 학창 시절)",
    "space": "장소·공간·배경 (예: 골목, 할머니 집, 바다, 운동장, 놀이공원)",
    "temperature": "몸으로 느낀 따뜻함·차가움·더위·추위 (예: 따뜻하다, 시원하다, 입김)",
    "light": "빛·광원·날씨가 만드는 빛 (예: 햇살, 가로등, 촛불, 달빛, 비, 안개)",
    "sensory": "맛·냄새·소리·촉감 (예: 달콤하다, 빗소리, 꽃향기, 보드랍다)",
}
TEXTURE = {
    "소중한": ["소중한", "기억에 남는", "따뜻한", "애틋한"],
    "아련한": ["아련한", "희미한", "꿈같은", "스치는"],
    "생생한": ["생생한", "어제 같은", "또렷한", "선명한"],
}


# 구조화 출력용 JSON 스키마 (Claude output_config.format) — 객체마다 additionalProperties: false 필수
SPLIT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "texture": {"type": "string", "enum": ["소중한", "아련한", "생생한", "없음"]},
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {"axis": {"type": "string", "enum": list(AXES)},
                           "text": {"type": "string"},
                           "color_word": {"type": "string"}},
            "required": ["axis", "text", "color_word"],
            "additionalProperties": False}},
    },
    "required": ["summary", "texture", "items"],
    "additionalProperties": False,
}
PICK_SCHEMA = {
    "type": "object",
    "properties": {"answers": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "word": {"type": "string"}, "color": {"type": "string"}},
        "required": ["id", "color"],
        "additionalProperties": False}}},
    "required": ["answers"],
    "additionalProperties": False,
}


def team_axis_lines(corpus: Corpus) -> str:
    """팀장님 규칙 핵심어는 축이 정해져 있다 → 지시문에 그대로 박아 둔다."""
    by_axis: dict[str, list[str]] = {}
    for w in corpus.words.values():
        if w.get("team_group"):
            by_axis.setdefault(w["axis"], []).append(w["word"])
    return "\n".join(f"- {a}: {', '.join(sorted(v))}" for a, v in sorted(by_axis.items()))


def build_split_prompt(sentence: str, corpus: Corpus) -> str:
    axes = "\n".join(f"- {a}: {AXIS_GUIDE[a]}" for a in AXES)
    tex = "\n".join(f"- {k}: {', '.join(v)} 같은 느낌" for k, v in TEXTURE.items())
    return f"""당신은 MaC(Memory & Color)의 기억 문장 분석기입니다. 사용자의 기억 문장을 읽고 색을 정하는 데 쓸 단어들을 뽑습니다.
색 이름이나 HEX 는 절대 만들지 마십시오. 색은 다른 단계에서 KS 색이름표로만 정합니다.

[7개 축 — 단어는 가리키는 대상에 따라 정확히 하나의 축에만 넣습니다]
{axes}

[팀 규칙으로 축이 정해진 단어 — 문장에 나오면 반드시 이 축으로]
{team_axis_lines(corpus)}
- light: 햇살

[뽑는 방법]
1. 문장에 실제로 나온 대상·정서만 뽑습니다. 문장에 없는 것을 지어내지 않습니다.
2. text 는 짧은 기본형으로 씁니다. 명사는 명사 그대로(예: '할머니 집', '노란 우산'이 아니라 '우산'), 형용사·동사는 '~다' 기본형(예: '포근했던' → '포근하다').
3. 문장에 색이 직접 붙어 있으면(예: '노란 우산', '새빨간 노을') 색 표현을 color_word 에 원문 그대로 넣고, text 에는 대상만 넣습니다. 색이 없으면 color_word 는 빈 문자열.
4. 한 축에 단어는 최대 3개, 전체 최대 12개. 색과 관련 없는 말(조사, '그때', '나')은 뽑지 않습니다.
5. 같은 대상을 두 축에 중복해서 넣지 않습니다.

[기억 질감 — 문장 전체가 어떤 느낌으로 기억되는지 하나만]
{tex}
- 없음: 위 셋 중 어느 것도 뚜렷하지 않음

[한 줄 요약] 기억의 정서와 시공간을 담은 30자 안팎의 시적인 한국어 문장 하나.

[출력] 마크다운 없이 아래 형태의 JSON 하나만:
{{"summary": "...", "texture": "소중한|아련한|생생한|없음", "items": [{{"axis": "memory_color", "text": "우산", "color_word": "노란"}}]}}

[기억 문장]
{json.dumps(sentence, ensure_ascii=False)}
"""


def build_pick_prompt(sentence: str, tasks: list[dict]) -> str:
    """tasks: [{id, item, axis, kind: 'word'|'color', options: [...]}]
       word  → options = [{"id": word_id, "word": 표기, "colors": [KS 이름…]}]  답: {"id","word","color"}
       color → options = [KS 이름…]                                           답: {"id","color"}"""
    return f"""당신은 MaC 색 매칭 검토자입니다. 기억 문장 속 단어마다, 주어진 후보 중에서 가장 알맞은 것을 고릅니다.
규칙:
- 반드시 후보 목록 안에서만 고릅니다. 목록에 없는 단어·색 이름을 새로 쓰지 않습니다.
- kind 가 "word" 이면: 문장 속 그 단어와 뜻이 같거나 가장 가까운 후보 단어의 id 를 word 에, 그 후보의 colors 중 문장 맥락에 맞는 색 하나를 color 에 씁니다.
  뜻이 맞는 후보가 하나도 없으면 word 와 color 를 모두 "없음"으로 씁니다. 글자만 비슷하고 뜻이 다른 후보(예: 벌꿀 ↔ 꿀벌)는 고르지 않습니다.
- kind 가 "color" 이면: options 의 색 이름 중 문장 맥락에 가장 맞는 하나를 color 에 씁니다.
출력은 마크다운 없이 JSON 하나만: {{"answers": [{{"id": "t1", "word": "…", "color": "…"}}]}}

[기억 문장]
{json.dumps(sentence, ensure_ascii=False)}

[고를 것]
{json.dumps(tasks, ensure_ascii=False, indent=1)}
"""


def build_paint_prompt(summary: str, palette: list[dict], texture: str) -> str:
    """palette: [{name, hex, area}] (면적 큰 순). 칩 4개가 아니라 그림에 쓰는 전체 팔레트."""
    lines = "\n".join(f"- {p['name']} {p['hex']} : 화면의 약 {round(p['area'] * 100)}%" for p in palette)
    mood = {"아련한": "가장자리가 부드럽게 번지고 대비가 낮은", "생생한": "경계가 또렷하고 대비가 분명한",
            "소중한": "부드럽고 조화로운", "없음": "자연스러운"}.get(texture, "자연스러운")
    return f"""다음 기억을 추상 회화 한 점으로 그려 주세요.
기억: {summary}
- 사람·글자·숫자·로고 없이, 색면과 붓질로만 표현한 추상화
- {mood} 분위기
- 아래 색만 사용하고, 각 색이 차지하는 면적을 비율에 가깝게 맞출 것 (다른 색을 새로 섞어 넣지 말 것)
{lines}
"""
