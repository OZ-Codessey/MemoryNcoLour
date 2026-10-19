"""MaC 색 엔진 시험 — 진짜 Claude·Gemini·Voyage 없이 (가짜 함수로) 규칙과 결정성을 확인한다.

실행  python -m pytest tests/test_mac_color.py -q      (pytest 가 없으면)  python tests/test_mac_color.py
코퍼스 벡터(data/kscorpus/word_vectors.*)는 진짜 파일을 쓴다. 바깥 API 는 부르지 않는다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mac_color import MemoryStore, NoColors, analyze_sentence  # noqa: E402
from services.mac_color import keys, matcher, palette  # noqa: E402
from services.mac_color.data import load, norm_text  # noqa: E402

C = load()


# ---------------------------------------------------------------- 가짜 바깥 API
class FakeLLM:
    """나누기: 문장별로 정해 둔 답 / 고르기: 후보 첫 번째(또는 정해 둔 답)."""

    def __init__(self, splits: dict, picks: dict | None = None):
        self.splits, self.picks, self.calls = splits, picks or {}, []

    def __call__(self, prompt: str) -> dict:
        self.calls.append(prompt[:20])
        if "기억 문장 분석기" in prompt:
            sent = json.loads(prompt.rsplit("[기억 문장]", 1)[1].strip())
            return self.splits[sent]
        tasks = json.loads(prompt.rsplit("[고를 것]", 1)[1].strip())
        ans = []
        for t in tasks:
            if t["item"] in self.picks:
                ans.append({"id": t["id"], **self.picks[t["item"]]})
            elif t["kind"] == "color":
                ans.append({"id": t["id"], "color": t["options"][-1]})      # 일부러 '마지막' 색 → 고르기가 반영되는지 확인
            else:
                o = t["options"][0]
                ans.append({"id": t["id"], "word": o["id"], "color": o["colors"][0]})
        return {"answers": ans}


class FakeEmbedder:
    """질의 '축: 표기' → 코퍼스 벡터. alias 로 '코퍼스에 없는 말'이 어느 표기와 같은 뜻인지 흉내 낸다."""

    def __init__(self, alias: dict | None = None, noise: float = 0.0):
        self.alias, self.noise = alias or {}, noise
        self.rows = {f"{C.settings['axis_label'][r['axis']]}: {r['text']}": i for i, r in enumerate(C.vec_rows)}

    def __call__(self, texts):
        out = []
        for t in texts:
            t = self.alias.get(t, t)
            if t in self.rows:
                v = C.vectors[self.rows[t]].copy()
            else:
                v = np.random.default_rng(abs(hash(t)) % 2**32).standard_normal(C.vectors.shape[1]).astype(np.float32)
            if self.noise:
                v = v + self.noise * np.random.default_rng(7).standard_normal(v.shape).astype(np.float32)
            out.append(v / np.linalg.norm(v))
        return np.stack(out)


S1 = "비 오는 날 할머니 집 마루에서 노란 우산을 쓰고 귤을 까먹던 따뜻한 오후"
SPLIT1 = {"summary": "비 내리던 오후, 할머니 집 마루의 귤 향기", "texture": "소중한", "items": [
    {"axis": "light", "text": "비", "color_word": ""},
    {"axis": "space", "text": "할머니 집", "color_word": ""},
    {"axis": "space", "text": "마루", "color_word": ""},
    {"axis": "memory_color", "text": "우산", "color_word": "노란"},
    {"axis": "memory_color", "text": "귤", "color_word": ""},
    {"axis": "temperature", "text": "따뜻하다", "color_word": ""},
    {"axis": "time", "text": "오후", "color_word": ""}]}


def run(sentence, split, picks=None, alias=None, store=None, texture=None):
    if texture:
        split = {**split, "texture": texture}
    return analyze_sentence(sentence, llm=FakeLLM({sentence: split}, picks), embedder=FakeEmbedder(alias),
                            store=store or MemoryStore(), corpus=C)


# ---------------------------------------------------------------- 시험
def test_full_sentence():
    r = run(S1, SPLIT1)
    by = {i["text"]: i for i in r["items"]}
    assert by["우산"]["method"] == "explicit" and by["우산"]["color"] == "노랑"          # 명시 색 최우선
    assert by["귤"]["method"] == "exact" and by["귤"]["color"] == "귤색"
    assert by["할머니 집"]["word_id"] == "space:외갓집"                                  # 별칭 정확 일치
    assert by["마루"]["axis"] == "space"                                                # 팀장님 규칙 축
    assert len(r["palette"]) == 4 and [c["weight"] for c in r["palette"]] == [0.45, 0.30, 0.15, 0.10]
    assert all(c["name"] in C.colors for c in r["palette"])                              # 칩은 KS 이름만
    assert abs(sum(p["area"] for p in r["paint_palette"]) - 1) < 1e-4
    assert r["specimen_code"].startswith("MaC-")


def test_context_color_pick_applied():
    # 오후 = 맥락 단어(병아리색;크림색). 가짜 LLM 은 '마지막' 후보를 고른다 → 크림색이어야 함
    r = run(S1, SPLIT1)
    assert {i["text"]: i for i in r["items"]}["오후"]["color"] == "크림색"


def test_area_rules():
    r = run(S1, SPLIT1)
    w = palette.team_weights()
    present = {"light", "space", "memory_color", "temperature", "time"}
    tot = sum(w[a] for a in present)
    expect_space_each = w["space"] / tot / 2                                             # 공간 두 단어가 반씩
    pal = palette.build_palette(_matches(SPLIT1["items"]), "소중한", C)
    space = [s for e in pal["entries"] for s in e["sources"] if s["axis"] == "space"]
    assert all(abs(s["area"] - round(expect_space_each, 6)) < 1e-6 for s in space)
    assert abs(sum(e["area"] for e in pal["entries"]) - 1) < 1e-4
    assert r["palette"]


def _matches(items, picks=None):
    ms, tasks = matcher.match_items(items, C, FakeEmbedder())
    matcher.apply_picks(ms, tasks, FakeLLM({}, picks)("[고를 것]\n" + json.dumps(tasks, ensure_ascii=False))["answers"]
                        if tasks else [], C)
    return ms


def test_same_hex_merged():
    items = [{"axis": "memory_color", "text": "개나리", "color_word": ""},           # 개나리색
             {"axis": "memory_color", "text": "병아리", "color_word": "노란"}]      # 명시 노랑 = 개나리색과 같은 hex
    pal = palette.build_palette(_matches(items), "없음", C)
    hexes = [e["hex"] for e in pal["entries"]]
    assert len(hexes) == len(set(hexes)) == 1
    assert pal["n_derived"] == 3


def test_one_color_three_derived_and_direction():
    items = [{"axis": "memory_color", "text": "귤", "color_word": ""}]
    for tex in ("아련한", "생생한", "소중한", "없음"):
        pal = palette.build_palette(_matches(items), tex, C)
        assert pal["n_extracted"] == 1 and pal["n_derived"] == 3
        assert pal["chips"][0]["name"] == "귤색" and all(c["derived"] for c in pal["chips"][1:])
        assert len({c["hex"] for c in pal["chips"]}) == 4
        assert abs(sum(p["area"] for p in pal["paint_palette"]) - 1) < 1e-4


def test_no_color_word_dropped_unless_explicit():
    a = _matches([{"axis": "memory_color", "text": "휴대폰", "color_word": ""}])      # 색 없음 목록의 단어
    assert a[0].method == "no_color" and a[0].color is None
    b = _matches([{"axis": "memory_color", "text": "휴대폰", "color_word": "빨간"}])
    assert b[0].color == "빨강"
    c = _matches([{"axis": "memory_color", "text": "휴대폰", "color_word": "노랗던"}])   # 활용형
    assert c[0].color == "노랑"


def test_nothing_found_raises():
    try:
        palette.build_palette(_matches([{"axis": "memory_color", "text": "휴대폰", "color_word": ""}]), "없음", C)
        raise AssertionError("NoColors 가 나와야 함")
    except NoColors:
        pass


def test_embedding_accept_and_pick_paths():
    # '밀감 한 봉지' 는 코퍼스에 없다 → 임베딩이 '사물: 귤' 과 같다고 흉내 → τ 이상이면 바로 받아들임
    ms, tasks = matcher.match_items([{"axis": "memory_color", "text": "밀감 한 봉지", "color_word": ""}], C,
                                    FakeEmbedder({"사물: 밀감 한 봉지": "사물: 귤"}))
    assert ms[0].method == "embedding" and ms[0].color == "귤색" and not tasks
    # 아무 표기와도 비슷하지 않음(무작위) → Gemini 후보 고르기로
    ms, tasks = matcher.match_items([{"axis": "memory_color", "text": "zzqq", "color_word": ""}], C, FakeEmbedder())
    assert tasks and tasks[0]["kind"] == "word" and len(tasks[0]["options"]) <= matcher.TOPK
    # Gemini 가 후보 밖 답을 하면 버린다
    matcher.apply_picks(ms, tasks, [{"id": tasks[0]["id"], "word": "memory_color:없는단어", "color": "빨강"}], C)
    assert ms[0].method == "dropped" and ms[0].color is None


def test_embedder_failure_falls_back_to_lexical():
    def broken(_):
        raise RuntimeError("limit")
    ms, tasks = matcher.match_items([{"axis": "memory_color", "text": "귤껍질 냄새나는 손", "color_word": ""}], C, broken)
    assert tasks and "글자 비교" in ms[0].note


def test_axis_corrected_on_exact_match():
    ms = _matches([{"axis": "memory_color", "text": "노을", "color_word": ""}])       # 팀장님 규칙: 노을은 time
    assert ms[0].axis == "time" and "축 고침" in ms[0].note


def test_determinism_100_runs_and_cache():
    first = run(S1, SPLIT1)
    for _ in range(100):
        r = run(S1, SPLIT1)                                                              # 매번 새 저장소
        assert r["specimen_hash"] == first["specimen_hash"]
        assert [(c["name"], c["hex"]) for c in r["palette"]] == [(c["name"], c["hex"]) for c in first["palette"]]
    store = MemoryStore()
    a = run(S1, SPLIT1, store=store)
    b = analyze_sentence(S1, llm=lambda p: (_ for _ in ()).throw(RuntimeError("부르면 안 됨")),
                         embedder=None, store=store, corpus=C)                            # 두 번째는 저장된 결과
    assert b["cached"] and b["specimen_hash"] == a["specimen_hash"]
    assert keys.sentence_key(" " + S1 + "  ") == keys.sentence_key(S1)                   # 앞뒤·연속 공백 무시


def test_sentence_stored_with_key():
    store = MemoryStore()
    r = run(S1, SPLIT1, store=store)
    saved = store.d[keys.sentence_key(S1)]
    assert saved["sentence"] == S1 and saved["sentence_key"] == keys.sentence_key(S1)
    assert saved["memory_summary"] == r["memory_summary"] and len(saved["palette"]) == 4


def test_all_outputs_are_ks_names():
    r = run(S1, SPLIT1)
    names = [c["name"] for c in r["palette"]] + [p["name"] for p in r["paint_palette"]]
    assert all(n in C.colors for n in names)
    assert all(norm_text(n) == n for n in names)


# ---------------------------------------------------------------- Claude 나누기 (HTTP 를 가짜로)
class _Resp:
    def __init__(self, code, data=None, headers=None):
        self.status_code, self._d, self.headers = code, data or {}, headers or {}

    def json(self):
        return self._d


def _claude_ok(obj, fenced=False, stop="end_turn"):
    t = json.dumps(obj, ensure_ascii=False)
    if fenced:
        t = "```json\n" + t + "\n```"
    return _Resp(200, {"content": [{"type": "text", "text": t}], "stop_reason": stop})


def _patch_post(responses):
    import os
    import requests
    from services.mac_color import clients
    calls = []
    seq = list(responses)
    old = (requests.post, clients.time.sleep, os.environ.get("ANTHROPIC_API_KEY"))
    def fake(url, json=None, headers=None, timeout=None):                # noqa: A002
        calls.append({"url": url, "body": json, "headers": headers})
        return seq.pop(0)
    requests.post, clients.time.sleep = fake, (lambda s: None)
    clients._variant_start = 0
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    def undo():
        requests.post, clients.time.sleep = old[0], old[1]
        clients._variant_start = 0
        if old[2] is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = old[2]
    return calls, undo


def test_claude_splitter_same_result():
    from services.mac_color import prompts
    from services.mac_color.clients import make_llm
    calls, undo = _patch_post([_claude_ok(SPLIT1)])
    try:
        r = analyze_sentence(S1, splitter=make_llm("claude", prompts.SPLIT_SCHEMA), llm=FakeLLM({}),
                             embedder=FakeEmbedder(), store=MemoryStore(), corpus=C)
    finally:
        undo()
    base = run(S1, SPLIT1)
    assert r["specimen_code"] == base["specimen_code"]                                  # 나누기 답이 같으면 칩도 같다
    c = calls[0]
    assert c["url"].endswith("/v1/messages") and c["headers"]["anthropic-version"] == "2023-06-01"
    assert c["headers"]["x-api-key"] == "test-key" and "test-key" not in json.dumps(c["body"])
    assert c["body"]["output_config"]["format"]["schema"] == prompts.SPLIT_SCHEMA
    assert "기억 문장 분석기" in c["body"]["messages"][0]["content"]
    assert r["version"]["splitter"].startswith("claude:") and r["version"]["picker"] == "custom"


def test_claude_fallbacks():
    from services.mac_color.clients import ClaudeError, claude_json
    from services.mac_color.pipeline import SplitFailed, split
    # 400 → effort 빼고 → 400 → 구조화 출력 빼고 (```json 감싼 답도 읽음)
    calls, undo = _patch_post([_Resp(400), _Resp(400), _claude_ok({"a": 1}, fenced=True), _claude_ok({"d": 4})])
    try:
        assert claude_json("x", {"type": "object"}) == {"a": 1}
        oc = [c["body"].get("output_config", {}) for c in calls]
        assert "effort" in oc[0] and "format" in oc[0] and "effort" not in oc[1] and "format" in oc[1] and oc[2] == {}
        # 통한 단계를 기억 → 다음 문장은 거절 요청 없이 바로 (옵션을 받지 않는 모델, 예: Codyssey claude-sonnet-4)
        assert claude_json("y", {"type": "object"}) == {"d": 4}
        assert len(calls) == 4 and "output_config" not in calls[3]["body"]
    finally:
        undo()
    # 429 → 한 번만 다시
    calls, undo = _patch_post([_Resp(429, headers={"retry-after": "1"}), _claude_ok({"b": 2})])
    try:
        assert claude_json("x") == {"b": 2} and len(calls) == 2
    finally:
        undo()
    # 429 두 번 → 실패, 나누기는 다시 부르지 않음
    calls, undo = _patch_post([_Resp(429), _Resp(429, {"error": {"type": "rate_limit_error"}})])
    try:
        try:
            split(S1, C, lambda p: claude_json(p))
            raise AssertionError("실패해야 함")
        except SplitFailed as e:
            assert "rate_limit_error" in str(e) and len(calls) == 2
    finally:
        undo()
    # 답이 잘림(max_tokens) → 오류
    calls, undo = _patch_post([_claude_ok({"c": 3}, stop="max_tokens")])
    try:
        try:
            claude_json("x")
            raise AssertionError("실패해야 함")
        except ClaudeError:
            pass
    finally:
        undo()


def test_split_schema_is_strict():
    from services.mac_color import prompts
    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "object":
                assert n.get("additionalProperties") is False
            for bad in ("maxItems", "minimum", "maximum", "minLength", "maxLength"):
                assert bad not in n                                     # 구조화 출력이 받지 않는 제약
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(prompts.SPLIT_SCHEMA)
    walk(prompts.PICK_SCHEMA)
    from services.mac_color.data import AXES
    assert prompts.SPLIT_SCHEMA["properties"]["items"]["items"]["properties"]["axis"]["enum"] == AXES


def test_env_file_rules(tmp=None):
    """.env: 아래 줄이 이김 · 자리 표시 값('여기에_발급받은_키')은 없는 것 · 한글 섞인 키는 원인을 알려 줌"""
    import os
    import tempfile
    from services.mac_color import clients
    names = ("MAC_T_A", "MAC_T_B", "MAC_T_C")
    old = {n: os.environ.pop(n, None) for n in names}
    try:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            f.write_text("MAC_T_A=여기에_발급받은_키\nMAC_T_A=pa-real123\nMAC_T_B=여기에_발급받은_키\n"
                         "MAC_T_C=pa-abc 키\n", encoding="utf-8")
            clients.load_env_file(f)
            assert os.environ["MAC_T_A"] == "pa-real123"            # 아래 줄이 이김
            assert "MAC_T_B" not in os.environ                      # 자리 표시 값은 무시
            orig = clients.load_env_file
            clients.load_env_file = lambda path=None: None
            try:
                assert clients.api_key("MAC_T_B") is None
                try:
                    clients.api_key("MAC_T_C")
                    raise AssertionError("실패해야 함")
                except RuntimeError as e:
                    assert "MAC_T_C" in str(e) and "pa-abc" not in str(e)   # 키 값은 메시지에 안 나옴
            finally:
                clients.load_env_file = orig
    finally:
        for n, v in old.items():
            os.environ.pop(n, None)
            if v is not None:
                os.environ[n] = v


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for f in fns:
        f()
        print("ok ", f.__name__)
    print(f"{len(fns)}개 통과")
