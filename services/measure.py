"""
================================================================================
MaC — 그림 → 칩 4개 (면적이 큰 순)
================================================================================
1. 그림을 작게 줄여(긴 변 200px) Lab 색공간으로 바꾼다.
2. k-means(k=8, 시드 고정 → 같은 그림이면 같은 결과)로 색 덩어리를 나눈다.
3. 같은 '색 계열'인 덩어리만 합친다 (가까운 쌍부터). 합칠 수 있는 조건 — 두 계열의 '모든' 원래 덩어리끼리 만족해야 함:
     · 무채색(C < NEUTRAL_C)끼리:           밝기 차 ΔL < NEUTRAL_DL          (회색과 짙은 남색은 따로)
     · 유채색끼리:                          색상각 차 < HUE_DH, ΔL < CHROMA_DL, ΔE < CHROMA_DE (주황끼리·갈색끼리)
     · 무채색 ↔ 유채색:                     채도 차 < NEUTRAL_C, ΔL < NEUTRAL_DL (거의 회색인 갈색만)
   → 주황이 세 덩어리로 쪼개져 순위에서 밀리는 것과, 칙칙한 갈색이 회색에 흡수돼 사라지는 것을 둘 다 막는다.
   (2026-10-20: 거리만 보고 ΔE 25 로 합쳤더니 장례식 그림의 갈색 띠가 회색에 흡수돼 칩에서 1% 로 나옴 → 이 규칙으로 바꿈)
4. 계열마다 '대표 색' = 그 계열에서 가장 큰 원래 덩어리의, 중심에 가까운 픽셀(상위 CORE_FRAC) 평균에 가장 가까운 실제 픽셀.
   (계열 전체 평균을 쓰면 서로 다른 톤이 섞여 탁한 색이 나온다)
5. 면적 큰 순으로 4개를 고르되, 이미 고른 칩과 ΔE < DUP_DE 인 '거의 같은 색'(예: 한 톤 차이 회색)은 따로 칩을 만들지 않고
   그 칩의 면적에 더한다 → 그 자리에 다음으로 큰 '다른 색'(예: 작은 호박색 붓질)이 들어온다.
   다른 색이 모자라 4개가 안 되면 합쳤던 것을 다시 칩으로 살린다.
   (2026-10-20: 빗속 정류장 그림에서 회색 두 톤이 칩 2개를 차지하고 호박색 포인트가 빠짐 → 추가)
   칩 4개 → 비율은 비율은 4개 합 100% 로 다시 나눈 값(ratio)과 그림 전체 대비(share) 둘 다 준다.
6. 칩 이름: 최종 표시 이름은 KS A 0011 한국어 색이름 (services/ks_names.py — 관용색 ΔE00 ≤ 5 면 관용색이름,
   아니면 계통색이름). 아래 규칙은 '이유(reason)'와 영문 이름(color_name_en)을 정하는 데 쓴다:
   - '가까움'은 색 계열을 고려한 거리: 유채색 칩 ↔ 무채색 계획 색(또는 반대)은 ΔE 에 HUE_PENALTY 를 더한다
   - 가장 가까운 '계획 색'(Claude 팔레트)과 ΔE ≤ NAME_DE → 그 색의 영문 이름·이유·역할을 물려받는다 (name_source=plan)
     같은 색상(색상각 차 < SAME_HUE_DH)의 유채색이면 ΔE ≤ SAME_HUE_DE 까지 물려받는다 (예: 같은 주황의 더 어두운 갈색)
   - 아니면서 가장 가까운 두 계획 색 사이에서 섞인 색이면(둘 다 ΔE ≤ MIX_DE) → 묘사 영문 이름 + "A · B 가 섞여 나온 색"
     (name_source=mixed)
   - 그 밖 → 묘사 영문 이름만 (name_source=described). 묘사 = 밝기·채도·색상 (예: Light Vivid Orange)
   - 계획 색 하나의 이름은 칩 하나만 물려받는다 (가장 가까운 칩). 같은 계획 색에 더 가까운 칩이 이미 있으면
     묘사 이름 + "<계획 색> 의 더 밝은/어두운 톤" (name_source=tone) → 같은 이름이 두 번 나오지 않는다.
7. 표본 코드 MaC-L.C.H: 팀 api/analyze.py 의 compute_weighted_lch_hash 와 같은 식 (칩 4개 · ratio 가중).

numpy 만 쓴다 (scikit-learn 없음 → Vercel 함수 용량 부담 없음).
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

try:
    from services.ks_names import apply_ks_names, has_batchim, ks_color_name
except ImportError:
    from ks_names import apply_ks_names, has_batchim, ks_color_name


def ks_name(hx: str) -> str:
    try:
        return ks_color_name(hx)["name"]
    except Exception:
        return hx

K = 8
NEUTRAL_C = 8.0
NEUTRAL_DL = 10.0
HUE_DH = 25.0
CHROMA_DL = 30.0
CHROMA_DE = 35.0
CORE_FRAC = 0.4
NAME_DE = 18.0
SAME_HUE_DH = 20.0
SAME_HUE_DE = 28.0
MIX_DE = 30.0
HUE_PENALTY = 20.0
DUP_DE = 12.0
SIDE = 200


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = np.asarray(rgb, float) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def hex_to_rgb(hx: str) -> np.ndarray:
    return np.array([int(hx[i:i + 2], 16) for i in (1, 3, 5)], float)


def rgb_to_hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(round(v)) for v in rgb)


def _kmeans(X: np.ndarray, k: int, seed: int, iters: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """k-means++ 초기화 + Lloyd 반복. 결정론(시드 고정)."""
    rng = np.random.default_rng(seed)
    centers = [X[rng.integers(len(X))]]
    for _ in range(1, k):
        d2 = np.min(((X[:, None, :] - np.array(centers)[None]) ** 2).sum(-1), axis=1)
        probs = d2 / d2.sum() if d2.sum() > 0 else np.full(len(X), 1 / len(X))
        centers.append(X[rng.choice(len(X), p=probs)])
    C = np.array(centers)
    for _ in range(iters):
        lbl = ((X[:, None, :] - C[None]) ** 2).sum(-1).argmin(1)
        newC = np.array([X[lbl == j].mean(0) if (lbl == j).any() else C[j] for j in range(k)])
        if np.allclose(newC, C, atol=0.05):
            break
        C = newC
    lbl = ((X[:, None, :] - C[None]) ** 2).sum(-1).argmin(1)
    return C, lbl


_HUES = [(20, "Rose"), (44, "Red"), (52, "Red Orange"), (72, "Orange"), (92, "Amber"), (106, "Yellow"),
         (125, "Yellow Green"), (165, "Green"), (205, "Teal"), (245, "Cyan Blue"), (312, "Blue"), (322, "Violet"),
         (348, "Magenta"), (360, "Rose")]


def describe_name(hx: str) -> str:
    """HEX → 영문 묘사 이름 (예: Light Vivid Orange, Deep Muted Teal, Pale Warm Grey)."""
    L, a, b = rgb_to_lab(hex_to_rgb(hx))
    C, h = math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360
    light = "Deep" if L < 30 else "Dark" if L < 45 else "Medium" if L < 62 else "Light" if L < 80 else "Pale"
    if C < 8:
        tone = "" if C < 3 else (" Warm" if 20 <= h < 110 else " Cool")
        return f"{light}{tone} {'Off White' if L >= 92 else 'Grey'}"
    sat = "Muted" if C < 22 else "Soft" if C < 45 else "Vivid"
    hue = next(n for bound, n in _HUES if h < bound)
    if 30 <= h < 95 and L < 52:                       # 어두운 주황·호박색 = 갈색
        return f"{'Deep' if L < 30 else 'Dark' if L < 42 else 'Warm'} {'Red Brown' if h < 52 else 'Brown'}"
    if 60 <= h < 110 and L >= 86 and C < 28:          # 아주 연한 노랑·호박색 = 크림
        return f"{'Soft' if C >= 12 else 'Pale'} Cream"
    return f"{light} {sat} {hue}"


def weighted_lch_hash(chips: list[dict]) -> str:
    """팀 api/analyze.py compute_weighted_lch_hash 와 같은 식: 칩 Lab 의 가중 평균 → L·C·H 정수."""
    tot = sum(c["ratio"] for c in chips) or 1.0
    L = a = b = 0.0
    for c in chips:
        w = c["ratio"] / tot
        l_, a_, b_ = rgb_to_lab(hex_to_rgb(c["hex"]))
        L, a, b = L + l_ * w, a + a_ * w, b + b_ * w
    C = math.hypot(a, b)
    H = math.degrees(math.atan2(b, a)) % 360
    return f"MaC-{int(round(L)):02d}.{int(round(C)):02d}.{int(round(H)):03d}"


def _same_family(p: np.ndarray, q: np.ndarray) -> bool:
    """두 Lab 색이 같은 색 계열인가 (3번 규칙)."""
    L1, a1, b1 = p
    L2, a2, b2 = q
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    dL = abs(L1 - L2)
    if C1 < NEUTRAL_C and C2 < NEUTRAL_C:
        return dL < NEUTRAL_DL
    if C1 >= NEUTRAL_C and C2 >= NEUTRAL_C:
        dh = abs(math.degrees(math.atan2(b1, a1)) - math.degrees(math.atan2(b2, a2))) % 360
        dh = min(dh, 360 - dh)
        return dh < HUE_DH and dL < CHROMA_DL and float(np.linalg.norm(p - q)) < CHROMA_DE
    return abs(C1 - C2) < NEUTRAL_C and dL < NEUTRAL_DL


def _chroma_hue(c):
    return math.hypot(c[1], c[2]), math.degrees(math.atan2(c[2], c[1])) % 360


def _same_hue(p, q) -> bool:
    (C1, h1), (C2, h2) = _chroma_hue(p), _chroma_hue(q)
    if C1 < NEUTRAL_C or C2 < NEUTRAL_C:
        return False
    dh = abs(h1 - h2) % 360
    return min(dh, 360 - dh) < SAME_HUE_DH


def _name_dist(chip, plan) -> float:
    """이름 붙일 때의 거리: 유채색 ↔ 무채색이면 벌점 (갈색 칩에 회색 이름이 붙지 않게)."""
    d = float(np.linalg.norm(chip - plan))
    if (_chroma_hue(chip)[0] < NEUTRAL_C) != (_chroma_hue(plan)[0] < NEUTRAL_C):
        d += HUE_PENALTY
    return d


def _pick_distinct(groups: list[dict], n: int) -> list[dict]:
    """면적 큰 순으로 n 개. 이미 고른 것과 ΔE < DUP_DE 면 그 칩의 면적에 더하고 다음 색을 본다 (5번 규칙)."""
    chosen, dups = [], []
    for g in groups:
        if len(chosen) == n:
            break
        near = min(chosen, key=lambda c: float(np.linalg.norm(c["lab"] - g["lab"])), default=None)
        if near is not None and float(np.linalg.norm(near["lab"] - g["lab"])) < DUP_DE:
            dups.append((g, near))
        else:
            chosen.append(g)
    while len(chosen) < n and dups:                  # 다른 색이 모자라면 합쳤던 것을 큰 것부터 되살림
        chosen.append(dups.pop(0)[0])
    out = [dict(c) for c in chosen]
    by_id = {id(c): o for c, o in zip(chosen, out)}
    for g, near in dups:
        by_id[id(near)]["share"] += g["share"]
    out.sort(key=lambda g: -g["share"])
    return out


def extract_chips(img: Image.Image, plan_palette: list[dict] | None = None, *, n: int = 4,
                  seed: int = 0) -> dict:
    """반환: {chips: [{hex, ratio, share, color_name, reason, role, nearest_de, inherited}], hash, clusters}"""
    small = img.convert("RGB").copy()
    small.thumbnail((SIDE, SIDE))
    rgb = np.asarray(small, float).reshape(-1, 3)
    lab = rgb_to_lab(rgb)

    C, lbl = _kmeans(lab, K, seed)
    groups = [{"idx": np.where(lbl == j)[0]} for j in range(K)]
    groups = [g for g in groups if len(g["idx"])]
    for g in groups:
        g["center"] = lab[g["idx"]].mean(0)
        g["parts"] = [g["idx"]]

    for g in groups:
        g["pcs"] = [g["center"]]                                    # 원래 덩어리 중심들 (합칠 조건 검사용)

    # 같은 색 계열끼리 합치기 (조건을 만족하는 쌍 중 가장 가까운 것부터)
    while len(groups) > n:
        best = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if not all(_same_family(x, y) for x in groups[i]["pcs"] for y in groups[j]["pcs"]):
                    continue
                d = float(np.linalg.norm(groups[i]["center"] - groups[j]["center"]))
                if best is None or d < best[0]:
                    best = (d, i, j)
        if best is None:
            break
        _, i, j = best
        idx = np.concatenate([groups[i]["idx"], groups[j]["idx"]])
        groups[i] = {"idx": idx, "center": lab[idx].mean(0), "parts": groups[i]["parts"] + groups[j]["parts"],
                     "pcs": groups[i]["pcs"] + groups[j]["pcs"]}
        groups.pop(j)

    total = len(lab)
    for g in groups:
        main = max(g["parts"], key=len)                                     # 계열에서 가장 큰 원래 덩어리
        pts = lab[main]
        center = pts.mean(0)
        dist = np.linalg.norm(pts - center, axis=1)
        core = pts[dist <= np.quantile(dist, CORE_FRAC)] if len(pts) >= 5 else pts
        target = core.mean(0)
        pick = main[np.linalg.norm(pts - target, axis=1).argmin()]          # 대표 = 실제 픽셀
        g["hex"] = rgb_to_hex(rgb[pick])
        g["share"] = len(g["idx"]) / total
    groups.sort(key=lambda g: -g["share"])
    for g in groups:
        g["lab"] = rgb_to_lab(hex_to_rgb(g["hex"]))
    top = _pick_distinct(groups, n)
    top_sum = sum(g["share"] for g in top) or 1.0

    plan_lab = [(p, rgb_to_lab(hex_to_rgb(p["hex"]))) for p in (plan_palette or [])]
    chip_labs = [rgb_to_lab(hex_to_rgb(g["hex"])) for g in top]

    # 이름 물려받기: (칩, 계획 색) 쌍을 가까운 순으로 — 계획 색 하나는 칩 하나에만
    def inheritable(ci, p, pl):
        de = float(np.linalg.norm(pl - chip_labs[ci]))
        return de <= NAME_DE or (_same_hue(chip_labs[ci], pl) and de <= SAME_HUE_DE)
    pairs = sorted((_name_dist(chip_labs[ci], pl), ci, pi) for ci in range(len(top)) for pi, (p, pl) in enumerate(plan_lab)
                   if inheritable(ci, p, pl))
    owner, used = {}, set()
    for _, ci, pi in pairs:
        if ci not in owner and pi not in used:
            owner[ci] = pi
            used.add(pi)

    chips = []
    for ci, g in enumerate(top):
        chip_lab = chip_labs[ci]
        ranked = sorted(((_name_dist(chip_lab, pl), float(np.linalg.norm(pl - chip_lab)), p, pl) for p, pl in plan_lab),
                        key=lambda t: t[0])
        near_de, near, near_lab = (round(ranked[0][1], 1), ranked[0][2], ranked[0][3]) if ranked else (None, None, None)
        if ci in owner:
            p = plan_lab[owner[ci]][0]
            near, near_de = p, round(float(np.linalg.norm(plan_lab[owner[ci]][1] - chip_lab)), 1)
            name, reason, role, source = p["color_name"], p.get("reason"), p.get("role"), "plan"
        elif near is not None and inheritable(ci, near, near_lab):       # 더 가까운 칩이 이미 이 이름을 가져감
            name, role, source = describe_name(g["hex"]), near.get("role"), "tone"
            tone_word = "밝은" if chip_lab[0] > near_lab[0] else "어두운"
            reason = f"{near.get('reason') or ks_name(near['hex'])} — 그림 속 조금 더 {tone_word} 톤"
        elif len(ranked) >= 2 and ranked[1][0] <= MIX_DE:
            a, b = ks_name(ranked[0][2]["hex"]), ks_name(ranked[1][2]["hex"])
            name, role, source = describe_name(g["hex"]), None, "mixed"
            reason = (f"{a}{'과' if has_batchim(a) else '와'} {b}{'이' if has_batchim(b) else '가'} "
                      f"그림 속에서 섞여 나온 색")
        else:
            name, reason, role, source = describe_name(g["hex"]), None, None, "described"
        chips.append({
            "hex": g["hex"],
            "ratio": round(g["share"] / top_sum, 4),          # 칩 4개 합 100%
            "share": round(g["share"], 4),                    # 그림 전체 대비
            "color_name": name,
            "reason": reason,
            "role": role,
            "name_source": source,
            "nearest_plan": near["hex"] if near else None,
            "nearest_de": near_de,
            "inherited": source == "plan",
        })
    # 칩 이름 = KS A 0011 한국어 색이름 (관용색 ΔE00 ≤ 5 → 관용색이름, 아니면 계통색이름). 영문은 color_name_en
    chips = apply_ks_names(chips)
    # 반올림 오차로 ratio 합이 1 이 아니면 가장 큰 칩에서 맞춘다
    diff = round(1 - sum(c["ratio"] for c in chips), 4)
    if chips and diff:
        chips[0]["ratio"] = round(chips[0]["ratio"] + diff, 4)
    return {"chips": chips, "hash": weighted_lch_hash(chips),
            "clusters": [{"hex": g["hex"], "share": round(g["share"], 4)} for g in groups]}
