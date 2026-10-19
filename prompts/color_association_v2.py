"""
================================================================================
MaC (Memory & Color) Architecture — Color Association Standard (v2.0)
================================================================================
[v1 → v2 에서 바뀐 전제]
v1: LLM 이 4색을 고정 면적비(0.45/0.30/0.15/0.10)로 바로 뽑아 칩으로 보여준다.
v2: LLM 이 '그림에 쓸 팔레트' 4~8색을 역할별 묶음으로 계획한다
    → 그 팔레트로 색면추상화를 그린다
    → 완성된 그림에서 면적이 큰 4색을 측정해 칩으로 만든다.
    (칩의 HEX·비율은 그림에서 잰 실제 값. 칩 이름·설명은 가장 가까운 계획 색에서 물려받는다.)

[유지한 것]
- 4역할 위계(주조·보조·분위기·강조)와 목표 면적 0.45/0.30/0.15/0.10 — 이제 '한 색'이 아니라 '색 묶음'의 몫
- 요소별 영향력 순서(COLOR_MAPPING_PRIORITY) 값 그대로
- 대원칙의 핵심(정서 우선 · 단일 색 고정 치환 금지 · 질감에 따른 채도/대비 · 날조 금지)

[바꾼 것]
- 정서 규칙: 긍정 4종 → 긍정·부정·복합 13종 (어두운 기억이 억지로 밝아지지 않도록)
- 기억 질감: '소중한' 1종 → 소중한·아련한·생생한·없음 4종 (그림의 경계 처리까지 연결)
- 시간·공간: 특정 사연(놀이공원·5월·아빠) 위주 → 일반 예시
- 감각(온도·빛·맛·소리·촉감) 규칙 추가 — 가중치만 있고 규칙이 없던 축
- 그림용 규칙(재료·붓질 에너지) 추가

이 파일은 '나침반'이다. 규칙의 예시 색 이름은 방향을 보여 줄 뿐 그대로 쓰라는 뜻이 아니다.
"""

VERSION = "assoc-v2.0"

# ------------------------------------------------------------------------------
# 1. 팔레트 위계 — 역할별 '색 묶음'과 목표 면적
# ------------------------------------------------------------------------------
PALETTE_SIZE = {"min": 4, "max": 8}     # v2.4: 4~8색 — 기억의 성격에 따라 LLM 이 정한다 (PALETTE_SIZE_GUIDE)
PALETTE_SIZE_GUIDE = {
    "4~5색": "고요하거나 단순한 기억 (평온·그리움·슬픔·외로움처럼 한 정서가 넓게 깔린 경우) — 넓은 색면 위주",
    "6색": "정서와 시공간이 함께 뚜렷한 보통의 기억",
    "7~8색": "풍성하고 생기 있는 기억 (축제·여행·여러 감각이 겹친 장면) — 다채로운 색 표현",
}

PALETTE_HIERARCHY = {
    "dominant": {
        "role": "Dominant (기저 지배색)",
        "target_share": 0.45,
        "colors": [1, 2],
        "purpose": "기억 전체를 감싸는 핵심 정서. 그림에서 가장 넓게 퍼지는 바탕 색면",
    },
    "supporting": {
        "role": "Supporting (배경 보조색)",
        "target_share": 0.30,
        "colors": [1, 3],
        "purpose": "시간·계절·장소를 받쳐 주는 구조적 색. 주조색 옆에서 두 번째로 넓은 색면",
    },
    "atmospheric": {
        "role": "Atmospheric (조도·공기색)",
        "target_share": 0.15,
        "colors": [1, 2],
        "purpose": "빛·공기·온도·감각의 정취. 색면 사이를 잇고 번지는 중간 영역",
    },
    "accent": {
        "role": "Accent (기억의 방점)",
        "target_share": 0.10,
        "colors": [1, 1],
        "purpose": "가장 또렷하게 남은 대상이나 감정. 화면 곳곳에 흩어진 작은 패치",
    },
}
ROLE_ORDER = ["dominant", "supporting", "atmospheric", "accent"]

# ------------------------------------------------------------------------------
# 2. 기억 요소별 영향력 (v1 값 그대로 — 순서 정보로 쓴다)
# ------------------------------------------------------------------------------
# memory_quality 는 색을 '정하는' 축이 아니라 정해진 색의 채도·대비·경계를 '조절하는' 값이다.
COLOR_MAPPING_PRIORITY = {
    "emotion": 0.30,          # 핵심 정서 → 주조색 묶음의 방향
    "memory_quality": 0.20,   # 기억 질감 → 채도·대비·경계 (조절값)
    "time": 0.15,             # 시간대·계절 → 명도, 보조색
    "space": 0.15,            # 공간 → 보조색
    "temperature": 0.10,      # 체감 온도 → 난색/한색 축
    "light": 0.05,            # 광원 → 분위기색
    "sensory": 0.05,          # 맛·소리·촉감 → 분위기색·강조색
}

# ------------------------------------------------------------------------------
# 3. 색채 연상 대원칙
# ------------------------------------------------------------------------------
COLOR_ASSOCIATION_PRINCIPLES = [
    "사물의 실제 표면색보다 기억에서 발생하는 정서적·감각적 색채연상을 우선한다.",
    "하나의 감정이나 단어를 특정한 단일 색으로 고정 치환하지 않는다. 아래 규칙의 예시 색은 방향일 뿐이다.",
    "같은 장소라도 누구와, 어떤 감정으로 있었는지에 따라 전혀 다른 색이 나올 수 있다.",
    "주조색 묶음은 시각적으로 가장 튀는 색이 아니라 기억 전체를 지배하는 기저 정서의 색이다.",
    "강조색 묶음은 면적은 가장 작지만 기억의 인상을 가장 강하게 남기는 색이다.",
    "정서가 슬프거나 쓸쓸하면 억지로 밝고 따뜻하게 만들지 않는다. 어두운 기억은 어두운 색으로 존중한다.",
    "오래되거나 아련한 기억은 채도와 명암 대비를 낮추고, 강렬하고 생생한 기억은 채도와 대비를 높인다.",
    "팔레트는 색상·명도·채도가 골고루 느껴지게 고른다: 밝은 색과 어두운 색, 탁한 색과 선명한 색이 함께 있어야 한다. "
    "어둡거나 아련한 기억도 그 정서 범위 안에서 폭을 둔다 (예: 어두운 바탕에 작은 밝은 색 하나). 거의 같은 색을 두 번 넣지 않는다.",
    "팔레트 전체는 하나의 기억에서 나온 하나의 색채 조화(Harmony)를 이룬다.",
    "사물의 고유색은 그 사물이 기억의 정서에 중요할 때만, 주로 강조색으로 쓴다.",
    "문장에 없는 인물·사물·사건을 지어내 색의 근거로 삼지 않는다.",
]

# ------------------------------------------------------------------------------
# 4. 정서 → 색 방향 (13종: 긍정·부정·복합)
# ------------------------------------------------------------------------------
# lightness / chroma / contrast: low · low-medium · medium · medium-high · high
# examples: 방향을 보여 주는 예시 이름(HEX 없음). 그대로 복사하지 않는다.
EMOTION_COLOR_RULES = {
    "warm_nostalgia": {
        "label": "따뜻한 그리움",
        "keywords": ["추억", "어린 시절", "포근함", "다정함", "옛날", "가족과 함께"],
        "lightness": "medium-high", "chroma": "low-medium", "temperature": "warm", "contrast": "low",
        "direction": "햇빛에 바랜 듯한 따뜻한 중간톤",
        "examples": ["Faded Apricot", "Honey Beige", "Dusty Rose"],
    },
    "joy_excitement": {
        "label": "기쁨·들뜸",
        "keywords": ["신남", "환희", "축제", "웃음", "들뜸", "즐거움"],
        "lightness": "high", "chroma": "high", "temperature": "warm", "contrast": "high",
        "direction": "밝고 선명한 난색에 맑은 대비색이 섞인 경쾌한 조합",
        "examples": ["Sunlit Coral", "Marigold", "Bright Aqua"],
    },
    "love_tenderness": {
        "label": "사랑·애틋함",
        "keywords": ["사랑", "애틋", "첫사랑", "고백", "두근", "설렘"],
        "lightness": "medium-high", "chroma": "medium", "temperature": "warm", "contrast": "low-medium",
        "direction": "부드러운 분홍·장밋빛에 은은한 보랏빛이 스미는 조합",
        "examples": ["Blush Pink", "Rosewood", "Lavender Haze"],
    },
    "calm_peace": {
        "label": "평온·여유",
        "keywords": ["평온", "고요", "잔잔", "여유", "휴식", "한적"],
        "lightness": "high", "chroma": "low", "temperature": "cool-neutral", "contrast": "low",
        "direction": "옅은 청록·회녹·흰빛이 넓게 펼쳐진 낮은 대비",
        "examples": ["Mist Blue", "Pale Sage", "Linen White"],
    },
    "stability_trust": {
        "label": "든든함·신뢰",
        "keywords": ["든든", "의지", "믿음", "보살핌", "안심", "곁에"],
        "lightness": "medium-low", "chroma": "low-medium", "temperature": "warm", "contrast": "medium",
        "direction": "흙과 나무 같은 깊은 난색 중간톤",
        "examples": ["Warm Walnut", "Olive Moss", "Soft Umber"],
    },
    "gratitude_pride": {
        "label": "감사·뿌듯함",
        "keywords": ["고마움", "감사", "뿌듯", "성취", "합격", "자랑스러움"],
        "lightness": "high", "chroma": "medium-high", "temperature": "warm", "contrast": "medium",
        "direction": "황금빛과 싱그러운 초록이 어우러진 밝은 조합",
        "examples": ["Golden Ochre", "Fresh Leaf", "Warm Ivory"],
    },
    "freedom_relief": {
        "label": "자유·홀가분함",
        "keywords": ["자유", "해방", "홀가분", "탁 트인", "떠나다", "숨통"],
        "lightness": "high", "chroma": "medium", "temperature": "cool-fresh", "contrast": "medium",
        "direction": "트인 하늘과 바닷빛의 시원한 색에 흰 공기",
        "examples": ["Open Sky", "Sea Glass", "Breeze Mint"],
    },
    "longing_wistful": {
        "label": "그리움·아련함",
        "keywords": ["그립다", "보고 싶다", "아련", "희미", "멀어진", "돌아갈 수 없는"],
        "lightness": "medium", "chroma": "low", "temperature": "cool-neutral", "contrast": "low",
        "direction": "해 질 녘의 흐린 라벤더·회보라에 안개 같은 회색",
        "examples": ["Dusk Lavender", "Hazy Lilac", "Fog Grey"],
    },
    "sadness_loss": {
        "label": "슬픔·이별·상실",
        "keywords": ["슬픔", "이별", "눈물", "상실", "떠나보낸", "마지막"],
        "lightness": "low-medium", "chroma": "low", "temperature": "cool", "contrast": "low-medium",
        "direction": "가라앉은 청회색·남색 중심, 따뜻한 색은 아주 작게",
        "examples": ["Slate Blue", "Rain Grey", "Deep Indigo"],
    },
    "loneliness": {
        "label": "외로움·쓸쓸함",
        "keywords": ["외로움", "쓸쓸", "혼자", "텅 빈", "적막", "아무도 없는"],
        "lightness": "medium-low", "chroma": "low", "temperature": "cool", "contrast": "medium",
        "direction": "넓고 차가운 탁색 위에 외따로 떨어진 작은 빛 하나",
        "examples": ["Cold Steel", "Muted Teal", "Pale Moonlight"],
    },
    "anxiety_tension": {
        "label": "불안·긴장",
        "keywords": ["불안", "긴장", "초조", "떨림", "걱정", "두려움"],
        "lightness": "medium", "chroma": "medium", "temperature": "cool-mixed", "contrast": "high",
        "direction": "탁한 회색 사이에 날카로운 노랑·멍든 보라가 고르지 않게 끼어듦",
        "examples": ["Acid Yellow", "Storm Grey", "Bruised Violet"],
    },
    "regret_guilt": {
        "label": "후회·미안함",
        "keywords": ["후회", "미안", "아쉬움", "놓친", "그때 왜"],
        "lightness": "medium-low", "chroma": "low", "temperature": "neutral", "contrast": "low",
        "direction": "빛바랜 자줏빛·잿빛 갈색처럼 색이 한 겹 덮인 느낌",
        "examples": ["Faded Mauve", "Ash Brown", "Smoky Blue"],
    },
    "anger_frustration": {
        "label": "화·억울함",
        "keywords": ["화가 나다", "억울", "분노", "답답", "짜증"],
        "lightness": "medium-low", "chroma": "high", "temperature": "warm", "contrast": "high",
        "direction": "짙은 진홍·탄 주황이 어두운 숯빛과 부딪힘",
        "examples": ["Deep Crimson", "Burnt Orange", "Charcoal"],
    },
}

# ------------------------------------------------------------------------------
# 5. 기억 질감 (조절값) — 색 선택 + 그림의 경계 처리까지
# ------------------------------------------------------------------------------
MEMORY_QUALITY_RULES = {
    "소중한": {
        "keywords": ["소중한", "기억에 남는", "따뜻한", "애틋한", "잊지 못할"],
        "chroma": "balanced", "contrast": "harmonious", "lightness": "그대로",
        "edges": "soft but readable transitions",
    },
    "아련한": {
        "keywords": ["아련한", "희미한", "꿈같은", "스치는", "오래된", "어렴풋한"],
        "chroma": "낮춘다", "contrast": "낮춘다", "lightness": "안개처럼 살짝 밝게",
        "edges": "edges dissolve almost completely, hazy veils",
    },
    "생생한": {
        "keywords": ["생생한", "어제 같은", "또렷한", "선명한", "아직도"],
        "chroma": "높인다", "contrast": "높인다", "lightness": "그대로",
        "edges": "soft transitions with a few crisper strokes",
    },
    "없음": {
        "keywords": [],
        "chroma": "balanced", "contrast": "natural", "lightness": "그대로",
        "edges": "naturally soft transitions",
    },
}

# ------------------------------------------------------------------------------
# 6. 시간·계절 / 공간 (예시 — 목록에 없어도 같은 방식으로 판단)
# ------------------------------------------------------------------------------
TIME_COLOR_RULES = {
    "dawn":      {"keywords": ["새벽", "동틀 무렵"], "lightness": "low-medium", "chroma": "low", "direction": "푸른 보랏빛 어스름"},
    "morning":   {"keywords": ["아침", "등굣길", "출근길"], "lightness": "high", "chroma": "medium", "direction": "맑고 산뜻한 밝은 색"},
    "afternoon": {"keywords": ["오후", "낮", "한낮"], "lightness": "medium-high", "chroma": "medium", "direction": "따뜻하게 데워진 공기색"},
    "sunset":    {"keywords": ["노을", "해질녘", "황혼", "저녁"], "lightness": "medium", "chroma": "medium-high", "direction": "주황·분홍에서 보라로 넘어가는 띠"},
    "night":     {"keywords": ["밤", "한밤", "자정"], "lightness": "low", "chroma": "low-medium", "direction": "짙은 남색 바탕에 인공 불빛 몇 점"},
    "spring":    {"keywords": ["봄", "벚꽃 필 무렵", "새 학기"], "lightness": "high", "chroma": "medium", "direction": "연두·연분홍의 가벼운 색"},
    "summer":    {"keywords": ["여름", "한여름", "장마"], "lightness": "medium-high", "chroma": "high", "direction": "짙은 초록·청록·강한 햇빛"},
    "autumn":    {"keywords": ["가을", "단풍철"], "lightness": "medium", "chroma": "medium", "direction": "황토·적갈·겨자빛"},
    "winter":    {"keywords": ["겨울", "눈 오는 날", "연말"], "lightness": "medium-high", "chroma": "low", "direction": "흰빛·회청색 + 작은 따뜻한 불빛"},
}

SPACE_COLOR_RULES = {
    "sea_river":   {"keywords": ["바다", "강", "해변", "한강"], "direction": "물빛 청록·회청에 반사된 빛"},
    "forest":      {"keywords": ["숲", "산", "공원", "나무 그늘"], "direction": "깊은 초록·이끼색·나무 그늘의 어둠"},
    "city_night":  {"keywords": ["도시", "거리", "골목", "번화가"], "direction": "어두운 바탕에 네온·가로등의 점광"},
    "home":        {"keywords": ["집", "방", "부엌", "거실", "할머니 집"], "direction": "백열등·나무·천의 따뜻한 중간톤"},
    "school":      {"keywords": ["학교", "교실", "운동장", "도서관"], "direction": "형광등의 밝은 중성색·칠판의 짙은 초록"},
    "festive":     {"keywords": ["놀이공원", "축제", "콘서트", "시장"], "direction": "여러 난색 불빛이 흩어진 생기 있는 조합"},
}

# ------------------------------------------------------------------------------
# 7. 감각 (온도·빛·맛·소리·촉감) — 분위기색·강조색 방향
# ------------------------------------------------------------------------------
# 맛-색 대응은 교차감각 연구에서 반복 관찰된 경향(단맛-분홍/빨강, 신맛-노랑/연두,
# 쓴맛-갈색/검정/보라, 짠맛-흰색/회청)을 따른다. 경향일 뿐 고정 규칙이 아니다.
SENSORY_COLOR_RULES = {
    "temperature": {"따뜻함": "난색 쪽으로 기울임", "차가움": "한색 쪽으로 기울임", "더위": "채도 높은 난색", "추위": "흰빛·회청"},
    "light": {"햇살": "따뜻한 노랑빛", "형광등": "푸른기 도는 흰빛", "촛불·백열등": "주황빛 점광",
              "달빛": "은빛 회청", "네온": "선명한 분홍·청록 점광", "비·흐림": "회색 막"},
    "taste": {"달다": "부드러운 분홍·살구", "시다": "노랑·연두", "쓰다": "짙은 갈색·검보라", "짜다": "흰빛·회청",
              "맵다": "진한 빨강·주황"},
    "sound": {"고요함": "대비 낮게", "시끄러움·음악": "대비 높게, 작은 패치 많이"},
    "touch": {"보드랍다": "밝고 탁한 파스텔", "거칠다": "탁하고 어두운 흙빛"},
}

# ------------------------------------------------------------------------------
# 8. 그림 규칙 — 재료·붓질 에너지 (LLM 이 문장에 맞게 고른다)
# ------------------------------------------------------------------------------
PAINT_MEDIUM_RULES = {
    "oil":        {"when": "깊고 무게감 있는 정서, 오래 간직한 기억, 진하고 묵직한 분위기"},
    "acrylic":    {"when": "선명하고 생생한 기억, 활기찬 순간, 또렷한 현재형의 감정"},
    "watercolor": {"when": "아련하고 가벼운 기억, 흐릿하거나 투명한 분위기, 맑고 여린 감정"},
}
PAINT_ENERGY_RULES = {
    "calm":     {"when": "고요·평온·슬픔·그리움처럼 느리고 가라앉은 기억"},
    "moderate": {"when": "따뜻함·든든함·잔잔한 기쁨처럼 흐르는 듯한 기억"},
    "vivid":    {"when": "들뜸·설렘·불안·화처럼 빠르고 강한 기억"},
}
