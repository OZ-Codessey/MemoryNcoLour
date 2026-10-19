

"""core/llm.py — 특징 추출(LLM) + 임베딩(선택).



LLM_MODE = claude | stub (ANTHROPIC_API_KEY 없으면 자동 stub)

  - claude: Anthropic Messages API 구조화 출력 사용

  - stub: KB 키워드 스캔 기반 결정론 추출



임베딩은 GEMINI_API_KEY가 있을 때 Gemini를 사용한다.

없으면 kb.make_retriever가 ngram 방식으로 대체한다.

"""

from __future__ import annotations



import os

import logging

from typing import List, Optional



from pydantic import BaseModel, Field, field_validator



logger = logging.getLogger("uvicorn")



from oracle.color_abstraction import HUES as _HUES



HUE_NAMES = list(_HUES)



# Railway 환경변수가 설정되어 있으면 해당 값이 우선 적용된다.

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4")

ANTHROPIC_BASE_URL = os.getenv(

    "ANTHROPIC_BASE_URL",

    "https://copa.codyssey.kr",

)



EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")

EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))





class AxisFeature(BaseModel):

    phrase: str = Field(default="", description="문장 근거의 짧은 구")

    label: str = Field(default="", description="KB 라벨 id 또는 빈 문자열")





class ObjectFeature(BaseModel):

    """기억 속 사물 하나. 색은 등급으로만 받는다."""

    name: str = ""

    hue: str = ""

    lightness: int = 0

    chroma: int = 0





class AffectFeature(BaseModel):

    """기억 전체의 정서 등급."""

    valence: int = 0

    arousal: int = 0





class Features(BaseModel):

    emotion: AxisFeature

    time: AxisFeature

    space: AxisFeature

    memory_quality: AxisFeature

    objects: List[ObjectFeature] = Field(default_factory=list)

    affect: Optional[AffectFeature] = None

    memory_summary: str = ""



    @field_validator("objects", mode="before")

    @classmethod

    def _legacy_objects(cls, v):

        # 예전 형식의 문자열 목록도 지원한다.

        return [

            {"name": x} if isinstance(x, str) else x

            for x in (v or [])

        ]





def llm_mode() -> str:

    mode = os.getenv("LLM_MODE", "").lower()



    if mode == "stub":

        return "stub"



    return "claude" if os.getenv("ANTHROPIC_API_KEY") else "stub"





_client = None





def _claude():

    """Anthropic 호환 API 클라이언트를 한 번 생성해 재사용한다."""

    global _client



    if _client is None:

        import anthropic



        api_key = os.getenv("ANTHROPIC_API_KEY")

        if not api_key:

            raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")



        _client = anthropic.Anthropic(

            api_key=api_key,

            base_url=ANTHROPIC_BASE_URL,

            timeout=40.0,

            max_retries=1,

        )



    return _client





def structured(

    system: str,

    user: str,

    schema: dict,

    max_tokens: int = 800,

    model: str | None = None,

) -> dict:

    """구조화 출력 1회 호출 후 dict로 변환한다."""

    import json

    from anthropic import transform_schema



    logger.warning(
        "[LLM CONFIG] base_url=%s model=%s",
        ANTHROPIC_BASE_URL,
        model or CLAUDE_MODEL,
    )

    response = _claude().messages.create(

        model=model or CLAUDE_MODEL,

        max_tokens=max_tokens,

        system=system,

        messages=[{"role": "user", "content": user}],

        output_config={

            "format": {

                "type": "json_schema",

                "schema": transform_schema(schema),

            }

        },

    )



    if getattr(response, "stop_reason", None) == "refusal":

        raise RuntimeError("모델이 응답을 거절했습니다.")



    response_text = "".join(

        block.text

        for block in response.content

        if block.type == "text"

    )



    return json.loads(response_text)





def _tool(labels: dict) -> dict:

    """Features 스키마. label은 축별 허용 목록으로 제한한다."""



    def axis(ids):

        return {

            "type": "object",

            "properties": {

                "phrase": {

                    "type": "string",

                    "description": "문장에 근거한 짧은 구",

                },

                "label": {

                    "type": "string",

                    "enum": list(ids) + [""],

                },

            },

            "required": ["phrase", "label"],

        }



    return {

        "name": "report_features",

        "description": (

            "기억 문장의 네 축 특징과 사물을 보고한다. "

            "색 이름·HEX·숫자 색값은 넣지 않는다."

        ),

        "input_schema": {

            "type": "object",

            "properties": {

                "emotion": axis(labels["emotion"]),

                "time": axis(labels["time"]),

                "space": axis(labels["space"]),

                "memory_quality": axis(labels["memory_quality"]),

                "objects": {

                    "type": "array",

                    "maxItems": 3,

                    "items": {

                        "type": "object",

                        "properties": {

                            "name": {

                                "type": "string",

                                "description": "문장에 실제로 나온 사물 이름",

                            },

                            "hue": {

                                "type": "string",

                                "enum": HUE_NAMES,

                                "description": "사물의 기본 색상 계열",

                            },

                            "lightness": {

                                "type": "integer",

                                "description": "밝기 등급 1~9",

                            },

                            "chroma": {

                                "type": "integer",

                                "description": "채도 등급 0~5",

                            },

                        },

                        "required": [

                            "name",

                            "hue",

                            "lightness",

                            "chroma",

                        ],

                    },

                },

                "affect": {

                    "type": "object",

                    "properties": {

                        "valence": {

                            "type": "integer",

                            "description": "쾌 등급 1~5",

                        },

                        "arousal": {

                            "type": "integer",

                            "description": "각성 등급 1~5",

                        },

                    },

                    "required": ["valence", "arousal"],

                },

                "memory_summary": {"type": "string"},

            },

            "required": [

                "emotion",

                "time",

                "space",

                "memory_quality",

                "objects",

                "affect",

                "memory_summary",

            ],

        },

    }





def extract_features(masked_text: str, kb) -> Features:

    if llm_mode() == "stub":

        return _stub_extract(masked_text, kb)



    from prompts.feature_prompt import build_feature_prompt



    labels = {

        axis: [entry["id"] for entry in kb.by_axis[axis]]

        for axis in ("emotion", "time", "space", "quality")

    }

    labels["memory_quality"] = labels.pop("quality")



    schema = _tool(labels)["input_schema"]

    last_error = None



    for _ in range(2):

        try:

            data = structured(

                build_feature_prompt(labels),

                f'기억 문장: "{masked_text}"',

                schema,

            )

            return Features.model_validate(data)



        except Exception as ex:

            last_error = ex

            logger.warning(

                "[LLM] 추출 실패 재시도: %s: %s",

                type(ex).__name__,

                ex,

            )



    raise RuntimeError(f"특징 추출 실패: {last_error}")





def _stub_extract(text: str, kb) -> Features:

    """키 없이 동작하는 결정론 추출."""



    def pick(axis):

        best = None



        for entry in kb.by_axis[axis]:

            for keyword in entry.get("keywords", []):

                index = text.find(keyword)



                if index >= 0 and (

                    best is None or index < best[0]

                ):

                    best = (index, keyword, entry["id"])



        if best:

            return AxisFeature(

                phrase=best[1],

                label=best[2],

            )



        return AxisFeature(

            phrase=text[:20],

            label="",

        )



    objects = []



    try:

        from core.objects import _lexicon



        found = sorted(

            (text.find(word), word)

            for word in _lexicon()

            if len(word) >= 2 and text.find(word) >= 0

        )



        from core.rag import get_rag



        rag = get_rag()



        if rag is not None:

            existing = {word for _, word in found}



            additional = [

                (text.find(word), word)

                for word in rag.objects_in(text, limit=3)

                if word not in existing

                and not any(

                    word in known or known in word

                    for known in existing

                )

            ]



            found = sorted(found + additional)



        objects = [

            {"name": word}

            for _, word in found[:3]

        ]



    except Exception:

        logger.warning(

            "[LLM] stub 사물 추출 실패",

            exc_info=True,

        )

        objects = []



    from core.affect import stub_affect



    emotion = pick("emotion")

    default_emotion = kb.by_id.get(emotion.label)



    if default_emotion is None:

        default_emotion = kb.by_id[kb.defaults["emotion"]]



    affect = stub_affect(text, default_emotion)



    return Features(

        emotion=emotion,

        time=pick("time"),

        space=pick("space"),

        memory_quality=pick("quality"),

        objects=objects,

        affect={

            "valence": affect["valence"],

            "arousal": affect["arousal"],

        },

        memory_summary=text[:30],

    )





def embed_texts(

    texts: list[str],

    task: str = "RETRIEVAL_DOCUMENT",

) -> list[list[float]]:

    """Gemini 임베딩을 사용한다."""

    if not os.getenv("GEMINI_API_KEY"):

        raise RuntimeError(

            "임베딩 제공자 미설정 (GEMINI_API_KEY 필요) "

            "— RETRIEVER=ngram 을 쓰세요"

        )



    from google import genai

    from google.genai import types



    response = genai.Client(

        api_key=os.environ["GEMINI_API_KEY"]

    ).models.embed_content(

        model=EMBED_MODEL,

        contents=texts,

        config=types.EmbedContentConfig(

            task_type=task,

            output_dimensionality=EMBED_DIM,

        ),

    )



    return [embedding.values for embedding in response.embeddings]
