"""いくつかの画面で使う まとめ方（種目・筋トレのまとめ・投稿の形）。"""

from collections import defaultdict
from datetime import date, timedelta

from fastapi import HTTPException
from sqlmodel import Session, and_, or_, select

from app import clock, config, rules, storage
from app.models import (Comment, Exercise, ExerciseNote, Menu, MenuItem, MenuPhoto, MenuTarget, Notice, Post, PostPhoto, Reaction,
                        Team, User, Workout, WorkoutSet)


def kind_of(exercise: Exercise | None) -> str:
    """種目の 記録の仕方（有酸素の部位なら いつも 時間・距離）。"""
    if exercise is None:
        return "weight_reps"
    return "cardio" if exercise.part == "cardio" else (exercise.kind or "weight_reps")


def default_rest(exercise: Exercise) -> int | None:
    """休憩の 最初の長さ（秒）。有酸素と、時間（分）の練習は タイマーなし。"""
    if kind_of(exercise) in ("cardio", "minutes"):
        return None
    if exercise.name in config.BIG3_EXERCISES:
        return config.REST_BIG3_SECONDS
    return config.REST_SECONDS_BY_PART.get(exercise.part, 120)


def team_sport(session: Session, team_id: int) -> str:
    team = session.get(Team, team_id)
    return team.sport if team else ""


def exercise_map(session: Session, team_id: int) -> dict[int, Exercise]:
    """このチームで使える種目（用意した種目 ＋ チームの競技向けの種目 ＋ チームで足した種目）。"""
    sport = team_sport(session, team_id)
    rows = session.exec(select(Exercise).where(or_(
        and_(Exercise.team_id == None, or_(Exercise.sport == None, Exercise.sport == sport)),  # noqa: E711
        Exercise.team_id == team_id,
    ))).all()
    return {e.id: e for e in rows}


def notes_by_workout(session: Session, workout_ids: list[int]) -> dict[int, dict[int, str]]:
    """種目ごとのメモ（筋トレ → 種目 → メモ）。"""
    notes: dict[int, dict[int, str]] = defaultdict(dict)
    if workout_ids:
        for n in session.exec(select(ExerciseNote).where(ExerciseNote.workout_id.in_(workout_ids))).all():
            notes[n.workout_id][n.exercise_id] = n.body
    return notes


def team_users(session: Session, team_id: int) -> dict[int, User]:
    return {u.id: u for u in session.exec(select(User).where(User.team_id == team_id)).all()}


def user_brief(user: User | None) -> dict:
    if not user:
        return {"id": 0, "name": "？", "role": "member", "position": ""}
    return {"id": user.id, "name": user.name, "role": user.role, "position": user.position or ""}


def positions_for(sport: str) -> list[str]:
    """その競技の ポジション（ない競技は 空）。"""
    return config.SPORT_POSITIONS.get(sport or "", [])


def position_order(positions: list[str]):
    """ポジションの順に 並べるための かぎ（決めていない人は 最後）。"""
    return lambda user: (positions.index(user.position) if user.position in positions else len(positions), user.name)


def get_team_post(session: Session, user: User, post_id: int) -> Post:
    """番号で投稿を取り出す。ほかのチームの投稿は「ない」ことにする。"""
    post = session.get(Post, post_id)
    if not post or post.team_id != user.team_id:
        raise HTTPException(404, "投稿が見つかりません")
    return post


def set_dict(s: WorkoutSet, kind: str) -> dict:
    return {"id": s.id, "label": rules.set_label(s, kind), "is_pr": s.is_pr, "weight": s.weight, "reps": s.reps,
            "seconds": s.seconds, "distance_m": s.distance_m, "duration_min": s.duration_min, "distance_km": s.distance_km}


def summarize(sets: list[WorkoutSet], exercises: dict[int, Exercise], notes: dict[int, str] | None = None) -> list[dict]:
    """セットを 種目ごとにまとめる（やった順）。種目ごとのメモも付ける。"""
    groups: dict[int, list[WorkoutSet]] = {}
    for s in sorted(sets, key=lambda s: s.id):
        groups.setdefault(s.exercise_id, []).append(s)
    result = []
    for exercise_id, group in groups.items():
        exercise = exercises.get(exercise_id)
        kind = kind_of(exercise)
        best = rules.best_set(group, kind) or group[0]
        result.append({
            "exercise_id": exercise_id,
            "name": exercise.name if exercise else "？",
            "part": exercise.part if exercise else "",
            "kind": kind,
            "count": len(group),
            "best": rules.set_label(best, kind),
            "has_pr": any(s.is_pr for s in group),
            "note": (notes or {}).get(exercise_id, ""),
            "sets": [set_dict(s, kind) for s in group],
        })
    return result


def recompute_prs(session: Session, user_ids: list[int], exercise: Exercise) -> None:
    """自己ベストの印を 付け直す（種目をまとめたとき・セットを直したとき）。古い順に見て、それまでの どれよりも強いセットに付ける。"""
    kind = kind_of(exercise)
    for user_id in user_ids:
        rows = session.exec(select(WorkoutSet).join(Workout, Workout.id == WorkoutSet.workout_id).where(
            Workout.user_id == user_id, WorkoutSet.exercise_id == exercise.id,
        ).order_by(Workout.started_at, WorkoutSet.id)).all()
        best = None
        for s in rows:
            value = rules.score(s, kind)
            s.is_pr = best is not None and value > best and kind != "cardio"
            best = value if best is None else max(best, value)


def sets_by_workout(session: Session, workout_ids: list[int]) -> dict[int, list[WorkoutSet]]:
    grouped: dict[int, list[WorkoutSet]] = defaultdict(list)
    if workout_ids:
        for s in session.exec(select(WorkoutSet).where(WorkoutSet.workout_id.in_(workout_ids))).all():
            grouped[s.workout_id].append(s)
    return grouped


def _reactions(rows: list[Reaction], users: dict[int, User], viewer_id: int) -> list[dict]:
    result = []
    for emoji in config.REACTIONS:
        mine = [r for r in rows if r.emoji == emoji]
        if mine:
            result.append({
                "emoji": emoji,
                "count": len(mine),
                "mine": any(r.user_id == viewer_id for r in mine),
                "names": [user_brief(users.get(r.user_id))["name"] for r in mine],
            })
    return result


def serialize_posts(session: Session, posts: list[Post], viewer: User) -> list[dict]:
    """投稿を 画面に出す形にする（筋トレのまとめ・写真・スタンプ・コメント付き）。"""
    if not posts:
        return []
    post_ids = [p.id for p in posts]
    users = team_users(session, viewer.team_id)
    exercises = exercise_map(session, viewer.team_id)
    workouts = {w.id: w for w in session.exec(select(Workout).where(Workout.id.in_([p.workout_id for p in posts]))).all()}
    sets = sets_by_workout(session, list(workouts))
    notes = notes_by_workout(session, list(workouts))
    menu_ids = [w.menu_id for w in workouts.values() if w.menu_id]
    menu_titles = dict(session.exec(select(Menu.id, Menu.title).where(Menu.id.in_(menu_ids))).all()) if menu_ids else {}

    photos: dict[int, list[PostPhoto]] = defaultdict(list)
    for photo in session.exec(select(PostPhoto).where(PostPhoto.post_id.in_(post_ids)).order_by(PostPhoto.position)).all():
        photos[photo.post_id].append(photo)
    comments: dict[int, list[Comment]] = defaultdict(list)
    for comment in session.exec(select(Comment).where(Comment.post_id.in_(post_ids)).order_by(Comment.id)).all():
        comments[comment.post_id].append(comment)
    comment_ids = [c.id for group in comments.values() for c in group]
    post_reactions: dict[int, list[Reaction]] = defaultdict(list)
    for r in session.exec(select(Reaction).where(Reaction.post_id.in_(post_ids)).order_by(Reaction.id)).all():
        post_reactions[r.post_id].append(r)
    comment_reactions: dict[int, list[Reaction]] = defaultdict(list)
    if comment_ids:
        for r in session.exec(select(Reaction).where(Reaction.comment_id.in_(comment_ids)).order_by(Reaction.id)).all():
            comment_reactions[r.comment_id].append(r)

    result = []
    for post in posts:
        workout = workouts.get(post.workout_id)

        def comment_dict(c: Comment) -> dict:
            author = users.get(c.user_id)
            return {
                "id": c.id,
                "user": user_brief(author),
                "parent_id": c.parent_id,
                "body": c.body,
                "created_at": c.created_at.isoformat(),
                "reactions": _reactions(comment_reactions[c.id], users, viewer.id),
                "can_delete": c.user_id == viewer.id or viewer.role == "trainer",
            }

        # トレーナーのコメントを 上に出す。返信は 元のコメントの下に 古い順で並べる
        top = [c for c in comments[post.id] if c.parent_id is None]
        ordered = sorted(top, key=lambda c: (user_brief(users.get(c.user_id))["role"] != "trainer", c.id))
        replies: dict[int, list[Comment]] = defaultdict(list)
        for c in comments[post.id]:
            if c.parent_id is not None:
                replies[c.parent_id].append(c)
        result.append({
            "id": post.id,
            "user": user_brief(users.get(post.user_id)),
            "body": post.body,
            "created_at": post.created_at.isoformat(),
            "reviewed": post.reviewed_at is not None,
            "mine": post.user_id == viewer.id,
            "can_delete": post.user_id == viewer.id or viewer.role == "trainer",
            # storage_key が空の写真は、保存期間をすぎて 消したもの
            "photos": [{"id": p.id, "url": f"/api/photos/{p.id}"} for p in photos[post.id] if p.storage_key],
            "photos_expired": any(not p.storage_key for p in photos[post.id]),
            "workout": {
                "id": post.workout_id,
                "day": workout.day.isoformat() if workout else None,
                "minutes": workout.minutes if workout else None,
                "backfill": bool(workout and workout.planned_minutes),  # あとから記録したもの
                "menu_title": menu_titles.get(workout.menu_id) if workout else None,
                "exercises": summarize(sets[post.workout_id], exercises, notes[post.workout_id]),
            },
            "reactions": _reactions(post_reactions[post.id], users, viewer.id),
            "comments": [{**comment_dict(c), "replies": [comment_dict(r) for r in replies[c.id]]} for c in ordered],
            "comment_count": len(comments[post.id]),
        })
    return result


def delete_post(session: Session, post: Post) -> None:
    """投稿を 写真・コメント・スタンプ・知らせごと消す（筋トレの記録は残る）。"""
    # ほかの表から指されているものを 後に消す（1つ消すごとに flush して、順番を守る）
    comment_ids = session.exec(select(Comment.id).where(Comment.post_id == post.id)).all()
    if comment_ids:
        for r in session.exec(select(Reaction).where(Reaction.comment_id.in_(comment_ids))).all():
            session.delete(r)
        session.flush()
    for model in (Reaction, Notice):
        for row in session.exec(select(model).where(model.post_id == post.id)).all():
            session.delete(row)
        session.flush()
    for is_reply in (True, False):  # 返信を先に消す（元のコメントを 指しているので）
        for row in session.exec(select(Comment).where(Comment.post_id == post.id)).all():
            if (row.parent_id is not None) == is_reply:
                session.delete(row)
        session.flush()
    for photo in session.exec(select(PostPhoto).where(PostPhoto.post_id == post.id)).all():
        if photo.storage_key:
            storage.delete(photo.storage_key)
        session.delete(photo)
    session.flush()
    session.delete(post)
    session.flush()


_expired_on: date | None = None  # 最後に 古い写真を片づけた日


def expire_photos(session: Session, force: bool = False) -> int:
    """保存期間をすぎた写真を消す（1日に1回だけ動く）。消した数を返す。

    写真そのものを消して、PostPhoto の行は storage_key を空にして残す（「写真があった」ことが わかるように）。
    """
    global _expired_on
    today = clock.today()
    if not force and _expired_on == today:
        return 0
    _expired_on = today
    cutoff = clock.now() - timedelta(days=config.PHOTO_KEEP_DAYS)
    rows = session.exec(select(PostPhoto).join(Post, Post.id == PostPhoto.post_id).where(
        Post.created_at < cutoff, PostPhoto.storage_key != "")).all()
    for photo in rows:
        storage.delete(photo.storage_key)
        photo.storage_key = ""
    session.commit()
    return len(rows)


def menu_status(menu: Menu) -> str:
    """今の期間か（now）、これからか（soon）、期間が終わったか（over）。"""
    today = clock.today()
    if menu.end_day and menu.end_day < today:
        return "over"
    if menu.start_day and menu.start_day > today:
        return "soon"
    return "now"


def menu_item_label(item: MenuItem, kind: str) -> str:
    """メニューの目標の書き方（例：60kg×8回×3セット、40m×10本×2セット、30分・5km）。"""
    if kind == "cardio":
        parts = [f"{item.minutes:g}分" if item.minutes else "", f"{item.distance_km:g}km" if item.distance_km else ""]
        return "・".join(p for p in parts if p) or "自由"
    if kind == "time":
        core = f"{item.seconds}秒"
    elif kind == "minutes":
        core = f"{(item.minutes or 0):g}分"
    elif kind == "count":
        core = f"{item.reps}本"
    elif kind == "distance_sets":
        core = f"{(item.distance_m or 0):g}m×{item.reps}本"
    elif kind == "reps":
        core = f"{item.reps}回"
    else:
        core = f"{item.weight:g}kg×{item.reps}回" if item.weight else f"{item.reps}回"
    return f"{core}×{item.sets}セット"


def menu_targets(session: Session, menu_ids: list[int]) -> dict[int, list[int]]:
    """メニューを配る相手（空なら 全員）。"""
    targets: dict[int, list[int]] = defaultdict(list)
    if menu_ids:
        for t in session.exec(select(MenuTarget).where(MenuTarget.menu_id.in_(menu_ids))).all():
            targets[t.menu_id].append(t.user_id)
    return targets


def menu_for(menu: Menu, user: User, targets: dict[int, list[int]]) -> bool:
    """この人に 配られたメニューか。"""
    return not targets.get(menu.id) or user.id in targets[menu.id]


def menu_dicts(session: Session, menus: list[Menu], exercises: dict[int, Exercise], with_targets: bool = False) -> list[dict]:
    """メニューを 画面に出す形にする（種目の名前・目標付き）。with_targets：配る相手も付ける（トレーナー用）。"""
    if not menus:
        return []
    targets = menu_targets(session, [m.id for m in menus]) if with_targets else {}
    names = {u.id: u.name for u in team_users(session, menus[0].team_id).values()} if with_targets else {}
    items: dict[int, list[MenuItem]] = defaultdict(list)
    rows = session.exec(select(MenuItem).where(MenuItem.menu_id.in_([m.id for m in menus])).order_by(MenuItem.position, MenuItem.id)).all()
    for item in rows:
        items[item.menu_id].append(item)
    photos: dict[int, list[MenuPhoto]] = defaultdict(list)
    for photo in session.exec(select(MenuPhoto).where(MenuPhoto.menu_id.in_([m.id for m in menus])).order_by(MenuPhoto.position, MenuPhoto.id)).all():
        photos[photo.menu_id].append(photo)
    return [{
        "id": m.id,
        "title": m.title,
        "note": m.note,
        "start_day": m.start_day.isoformat() if m.start_day else None,
        "end_day": m.end_day.isoformat() if m.end_day else None,
        "status": menu_status(m),
        "photos": [{"id": p.id, "url": f"/api/menu-photos/{p.id}"} for p in photos[m.id]],
        "active": m.active,
        "created_at": m.created_at.isoformat(),
        **({"targets": [{"id": uid, "name": names.get(uid, "？")} for uid in targets.get(m.id, [])]} if with_targets else {}),
        "items": [{
            "exercise_id": i.exercise_id,
            "name": exercises[i.exercise_id].name if i.exercise_id in exercises else "？",
            "part": exercises[i.exercise_id].part if i.exercise_id in exercises else "",
            "kind": kind_of(exercises.get(i.exercise_id)),
            "sets": i.sets,
            "reps": i.reps,
            "weight": i.weight,
            "seconds": i.seconds,
            "distance_m": i.distance_m,
            "minutes": i.minutes,
            "distance_km": i.distance_km,
            "label": menu_item_label(i, kind_of(exercises.get(i.exercise_id))),
        } for i in items[m.id]],
    } for m in menus]
