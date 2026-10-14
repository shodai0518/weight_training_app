"""筋トレの ルール（自己ベスト・セットの表示）。記録の仕方（kind）ごとに 比べ方と 書き方が ちがう。"""


def one_rep_max(weight: float | None, reps: int | None) -> float:
    """1回だけ持ち上げられる重さの 見つもり（Epley の式）。自重なら 回数で比べる。"""
    if not reps:
        return 0.0
    if not weight:
        return float(reps) / 100  # 自重の種目は 回数が多いほど 上（重さのある記録よりは下）
    return weight * (1 + reps / 30)


def guess_kind(s) -> str:
    """記録の仕方が わからないときに、入っている数字から決める。"""
    if s.seconds is not None:
        return "time"
    if s.distance_m is not None:
        return "distance_sets"
    if s.duration_min is not None or s.distance_km is not None:
        return "cardio"
    return "weight_reps"


def score(s, kind: str | None = None) -> float:
    """セットの強さ（大きいほど上）。自己ベストと「いちばん強いセット」に使う。有酸素は 比べない。"""
    kind = kind or guess_kind(s)
    if kind == "cardio":
        return 0.0
    if kind == "time":
        return float(s.seconds or 0)
    if kind == "minutes":
        return float(s.duration_min or 0)
    if kind == "distance_sets":
        return float((s.distance_m or 0) * (s.reps or 0))
    if kind in ("reps", "count"):
        return float(s.reps or 0)
    return one_rep_max(s.weight, s.reps)


def best_set(sets: list, kind: str | None = None) -> object | None:
    """いちばん強いセット。"""
    scored = [s for s in sets if score(s, kind) > 0]
    return max(scored, key=lambda s: score(s, kind), default=None)


def graph_point(s, kind: str | None = None) -> tuple[float, str]:
    """グラフに出す（値, 単位）。重さ×回数は 見つもり1RM（自重なら 回数）。"""
    kind = kind or guess_kind(s)
    if kind == "weight_reps":
        return (round(one_rep_max(s.weight, s.reps), 1), "kg") if s.weight else (float(s.reps or 0), "回")
    if kind == "time":
        return float(s.seconds or 0), "秒"
    if kind == "minutes":
        return float(s.duration_min or 0), "分"
    if kind == "count":
        return float(s.reps or 0), "本"
    if kind == "distance_sets":
        return round((s.distance_m or 0) * (s.reps or 0), 1), "m"
    return float(s.reps or 0), "回"


def set_label(s, kind: str | None = None) -> str:
    kind = kind or guess_kind(s)
    if kind == "cardio":
        parts = []
        if s.duration_min:
            parts.append(f"{s.duration_min:g}分")
        if s.distance_km:
            parts.append(f"{s.distance_km:g}km")
        return "・".join(parts) or "記録なし"
    if kind == "time":
        return f"{s.seconds}秒"
    if kind == "minutes":
        return f"{(s.duration_min or 0):g}分"
    if kind == "count":
        return f"{s.reps}本"
    if kind == "distance_sets":
        return f"{(s.distance_m or 0):g}m×{s.reps}本"
    if kind == "reps":
        return f"{s.reps}回"
    weight = f"{s.weight:g}kg×" if s.weight else "自重×"
    return f"{weight}{s.reps}回"
