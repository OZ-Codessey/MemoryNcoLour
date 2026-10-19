"""build_embeddings.py — 단어 코퍼스의 모든 표기를 Voyage 로 임베딩해 파일로 저장한다.

준비 (각자 자기 PC 에서, 한 번만)
  pip install voyageai numpy
  저장소 맨 위 폴더에 .env 파일을 만들고 한 줄:  VOYAGE_API_KEY=발급받은키
  (.env 는 절대 커밋하지 않는다 — .gitignore 에 .env 가 있는지 확인)

실행
  python scripts/build_embeddings.py --dry-run     # API 호출 없이 개수·예상 토큰만 확인
  python scripts/build_embeddings.py               # 실제 임베딩 (제한에 걸려도 기다렸다 다시 보냄)

입력   data/kscorpus/words.json        (build_words.py 결과)
출력   data/kscorpus/word_vectors.npz        벡터 (float16, 길이 1로 정규화)
       data/kscorpus/word_vectors.meta.json  각 벡터가 어느 단어·축인지 + 임베딩 설정(런타임 매칭이 그대로 따라 씀)

설정 (2026-10-18 비교 결과로 확정, scripts/compare_embeddings.py 의 F 설정)
  voyage-4-large · 1024차원 · input_type 없음 · 글자 형식 "{축}: {표기}" (예: "사물: 꿀벌")
  같은 문제에서 1등 색 정답 71%, 상위 20 후보 안 정답 97% — 단어만 넣은 voyage-4(52% / 89%)보다 크게 나음
  런타임에 사용자 단어를 임베딩할 때도 반드시 같은 모델·차원·형식을 쓴다 → meta.json 의 settings 를 읽어서 맞춤

다시 돌리면: 이미 만든 벡터(같은 설정·같은 글자)는 재사용하고 새로 생긴 표기만 API 로 보낸다.
키는 화면·파일 어디에도 출력하지 않는다. 벡터 파일에는 키가 들어가지 않으므로 커밋해도 된다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
KS = ROOT / "data" / "kscorpus"
WORDS = KS / "words.json"
OUT_NPZ = KS / "word_vectors.npz"
OUT_META = KS / "word_vectors.meta.json"

MODEL = "voyage-4-large"
DIM = 1024
INPUT_TYPE = None
TEXT_FORMAT = "{axis}: {text}"
AXIS_LABEL = {"emotion": "감정", "memory_color": "사물", "time": "시간", "space": "장소",
              "temperature": "온도", "light": "빛", "sensory": "감각", "none": "기타"}
BATCH = 1000              # 한 요청에 보내는 표기 수 (API 한도 1000). 무료 계정은 요청 횟수 제한이 낮아 크게 보내는 편이 빠름


def load_env_key() -> str | None:
    key = os.environ.get("VOYAGE_API_KEY")
    if key:
        return key.strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():   # 메모장 BOM 대비
            line = line.strip()
            if line.startswith("VOYAGE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def settings() -> dict:
    return {"model": MODEL, "dim": DIM, "input_type": INPUT_TYPE, "text_format": TEXT_FORMAT, "axis_label": AXIS_LABEL}


def settings_id() -> str:
    return json.dumps(settings(), ensure_ascii=False, sort_keys=True)


def format_text(text: str, axis: str) -> str:
    return TEXT_FORMAT.format(text=text, axis=AXIS_LABEL[axis])


def text_key(formatted: str) -> str:
    return hashlib.sha256((settings_id() + "\n" + formatted).encode()).hexdigest()[:16]


def collect(words: dict) -> list[dict]:
    """임베딩할 표기 목록. 한 표기 = 한 벡터 → 그 단어 줄(id)·축으로 이어진다."""
    rows = []
    for w in words["words"]:
        for s in [w["word"]] + w["aliases"]:
            f = format_text(s, w["axis"])
            rows.append({"text": s, "word_id": w["id"], "axis": w["axis"], "input": f, "key": text_key(f)})
    return rows


def load_cache() -> dict[str, np.ndarray]:
    if not (OUT_NPZ.exists() and OUT_META.exists()):
        return {}
    meta = json.loads(OUT_META.read_text(encoding="utf-8"))
    if meta.get("fake"):
        return {}                                   # 시험용 가짜 벡터는 절대 재사용하지 않는다
    if meta.get("settings_id") != settings_id():
        print("임베딩 설정이 바뀌어 기존 벡터를 쓰지 않고 전부 새로 만든다")
        return {}
    vecs = np.load(OUT_NPZ)["vectors"]
    return {r["key"]: vecs[i] for i, r in enumerate(meta["rows"])}


def embed_voyage(texts: list[str], key: str) -> tuple[np.ndarray, int]:
    import voyageai
    from voyageai import error as verr

    vo = voyageai.Client(api_key=key, max_retries=0)
    out, tokens = [], 0
    for i in range(0, len(texts), BATCH):
        part = texts[i:i + BATCH]
        wait = 20
        for attempt in range(8):
            try:
                r = vo.embed(part, model=MODEL, input_type=INPUT_TYPE, output_dimension=DIM)
                out.extend(r.embeddings)
                tokens += r.total_tokens
                break
            except verr.RateLimitError:
                print(f"  사용량 제한 — {wait}초 기다렸다 다시 보냄 ({attempt + 1}/8)")
                time.sleep(wait)
                wait = min(wait * 2, 120)
            except verr.AuthenticationError:
                sys.exit("API 키가 올바르지 않다. .env 의 VOYAGE_API_KEY 를 확인할 것 (키 값은 여기 출력하지 않음)")
            except (verr.ServiceUnavailableError, verr.ServerError, verr.APIConnectionError) as e:
                print(f"  일시 오류 {type(e).__name__} — {wait}초 뒤 다시")
                time.sleep(wait)
                wait = min(wait * 2, 120)
        else:
            sys.exit("여러 번 다시 보내도 실패했다. 잠시 뒤 다시 실행할 것 (이미 저장된 벡터는 재사용됨)")
        print(f"  {min(i + BATCH, len(texts))}/{len(texts)} 완료")
    return np.asarray(out, dtype=np.float32), tokens


def embed_fake(texts: list[str]) -> tuple[np.ndarray, int]:
    """API 없이 파이프라인만 시험할 때. 의미 없는 벡터 — word_vectors.FAKE.* 로만 저장되고 재사용되지 않는다."""
    rng = [np.random.default_rng(int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)).standard_normal(DIM) for t in texts]
    return np.asarray(rng, dtype=np.float32), 0


def main() -> None:
    for stream in (sys.stdout, sys.stderr):        # Windows 터미널에서 한글·기호 출력이 깨지지 않게
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 개수만 확인")
    ap.add_argument("--fake", action="store_true", help="시험용 가짜 벡터 (배포 금지)")
    ap.add_argument("--batch", type=int, default=BATCH)
    args = ap.parse_args()
    globals()["BATCH"] = max(1, min(args.batch, 1000))
    if args.fake:                                   # 가짜 벡터는 다른 이름으로만 저장 (실수로 커밋·배포 방지)
        globals()["OUT_NPZ"] = KS / "word_vectors.FAKE.npz"
        globals()["OUT_META"] = KS / "word_vectors.FAKE.meta.json"

    words = json.loads(WORDS.read_text(encoding="utf-8"))
    rows = collect(words)
    cache = {} if args.fake else load_cache()
    todo = sorted({r["input"] for r in rows if r["key"] not in cache})
    chars = sum(len(t) for t in todo)
    print(f"설정: {MODEL} · {DIM}차원 · 형식 '{TEXT_FORMAT}'")
    print(f"표기 {len(rows)}개 · 이미 있음 {sum(1 for r in rows if r['key'] in cache)} · 새로 만들 것 {len(todo)}개 "
          f"(예상 토큰 대략 {chars * 2:,} 이하)")
    if todo and len(todo) <= 20:
        print("  새로 만들 것: " + ", ".join(todo))
    if args.dry_run:
        return

    if todo:
        if args.fake:
            vecs, tokens = embed_fake(todo)
        else:
            key = load_env_key()
            if not key:
                sys.exit("VOYAGE_API_KEY 가 없다. 저장소 맨 위 .env 파일에 VOYAGE_API_KEY=... 한 줄을 넣을 것")
            vecs, tokens = embed_voyage(todo, key)
            print(f"사용한 토큰 {tokens:,}")
        for t, v in zip(todo, vecs):
            cache[text_key(t)] = v

    mat = np.stack([cache[r["key"]] for r in rows]).astype(np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    np.savez_compressed(OUT_NPZ, vectors=mat.astype(np.float16))
    meta = {
        "settings": settings(), "settings_id": settings_id(),
        "fake": bool(args.fake),
        "words_version": words["version"], "words_hash": words["content_hash"],
        "ks_colors_hash": words["ks_colors_hash"],
        "rows": [{"text": r["text"], "word_id": r["word_id"], "axis": r["axis"], "key": r["key"]} for r in rows],
    }
    OUT_META.write_text(json.dumps(meta, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"저장: {OUT_NPZ.relative_to(ROOT)} ({OUT_NPZ.stat().st_size / 1e6:.1f} MB), {OUT_META.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
