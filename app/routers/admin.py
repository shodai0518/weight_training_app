"""トレーナー用の管理画面。どの道すじも、サーバー側で トレーナーかを確かめる。"""

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app import auth, clock, config, rules, services
from app.db import get_session
from app.models import Comment, Exercise, ExerciseNote, MenuItem, Post, QuickReply, Team, User, UserExercise, Workout, WorkoutSet
from app.routers.users import count_role, member_row, member_stats, new_invite_code

router = APIRouter(prefix="/api/admin", dependencies=[Depends(auth.require_trainer)])


class MemberUpdate(BaseModel):
    role: str | None = None
    active: bool | None = None
    position: str | None = None  # ポジションを変える（空にすると 未設定）
    reset_password: bool = False


class QuickReplyCreate(BaseModel):
    body: str


class TeamUpdate(BaseModel):
    sport: str


class ExerciseRename(BaseModel):
    name: str


class ExerciseMerge(BaseModel):
    into_id: int  # この種目に まとめる


def inbox_query(user: User):
    """まだ どのトレーナーも見ていない投稿（自分の投稿は入れない）。"""
    return (Post.team_id == user.team_id, Post.reviewed_at == None, Post.user_id != user.id)  # noqa: E711


def previous_best(session: Session, workout: Workout, exercise_ids: list[int], exercises: dict) -> dict[int, WorkoutSet]:
    """種目ごとの「前回」（この筋トレより前で、その種目をやった いちばん新しい筋トレ）の いちばん強いセット。"""
    rows = session.exec(select(WorkoutSet, Workout.id).join(Workout, Workout.id == WorkoutSet.workout_id).where(
        Workout.user_id == workout.user_id, Workout.status == "done", Workout.started_at < workout.started_at,
        WorkoutSet.exercise_id.in_(exercise_ids),
    ).order_by(Workout.started_at.desc())).all()
    groups: dict[int, tuple[int, list[WorkoutSet]]] = {}
    for s, workout_id in rows:
        if s.exercise_id not in groups:
            groups[s.exercise_id] = (workout_id, [])
        if groups[s.exercise_id][0] == workout_id:
            groups[s.exercise_id][1].append(s)
    return {exercise_id: best for exercise_id, (_, group) in groups.items()
            if (best := rules.best_set(group, services.kind_of(exercises.get(exercise_id))))}


@router.get("/inbox")
def inbox(user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    """受信箱：まだ見ていない投稿を 古い順に。前回とのちがいと、前回のコメントも付ける。"""
    total = session.exec(select(func.count()).select_from(Post).where(*inbox_query(user))).one()
    posts = session.exec(select(Post).where(*inbox_query(user)).order_by(Post.id).limit(config.TIMELINE_PAGE)).all()
    items = services.serialize_posts(session, posts, user)
    trainer_ids = [u.id for u in services.team_users(session, user.team_id).values() if u.role == "trainer"]
    exercises = services.exercise_map(session, user.team_id)
    for post, item in zip(posts, items):
        workout = session.get(Workout, post.workout_id)
        sets = services.sets_by_workout(session, [workout.id])[workout.id]
        previous = previous_best(session, workout, [e["exercise_id"] for e in item["workout"]["exercises"]], exercises)
        for entry in item["workout"]["exercises"]:
            kind = entry["kind"]
            before = previous.get(entry["exercise_id"])
            now = rules.best_set([s for s in sets if s.exercise_id == entry["exercise_id"]], kind)
            if not before or not now:
                entry["trend"] = None  # 初めての種目・有酸素は 比べない
                continue
            a, b = rules.score(now, kind), rules.score(before, kind)
            entry["trend"] = {"dir": "up" if a > b else "down" if a < b else "same", "previous": rules.set_label(before, kind)}
        last = session.exec(select(Comment).join(Post, Post.id == Comment.post_id).where(
            Post.user_id == post.user_id, Post.id != post.id, Comment.user_id.in_(trainer_ids),
        ).order_by(Comment.id.desc())).first()
        item["last_trainer_comment"] = last.body if last else None
    return {"total": total, "posts": items}


@router.post("/posts/{post_id}/review")
def review(post_id: int, user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    """コメントなしで「見た」にする。"""
    post = services.get_team_post(session, user, post_id)
    if not post.reviewed_at:
        post.reviewed_by, post.reviewed_at = user.id, clock.now()
        session.commit()
    return {"ok": True}


@router.get("/team")
def team_info(user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    team = session.get(Team, user.team_id)
    stats = member_stats(session, user.team_id)
    positions = services.positions_for(team.sport)
    by_position = services.position_order(positions)
    users = sorted(services.team_users(session, user.team_id).values(), key=lambda u: (not u.active, u.role != "trainer", by_position(u)))
    return {
        "name": team.name,
        "sport": team.sport,
        "sports": config.SPORTS,
        "positions": positions,
        "invite_code": team.invite_code,
        "members": {"count": count_role(session, team.id, "member"), "max": team.max_members},
        "trainers": {"count": count_role(session, team.id, "trainer"), "max": team.max_trainers},
        "users": [{**member_row(u, stats), "login_id": u.login_id, "active": u.active, "me": u.id == user.id} for u in users],
    }


@router.post("/invite")
def regenerate_invite(user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    """招待の合言葉を作り直す（前の合言葉は 使えなくなる）。"""
    team = session.get(Team, user.team_id)
    team.invite_code = new_invite_code()
    session.commit()
    return {"invite_code": team.invite_code}


@router.patch("/members/{member_id}")
def update_member(member_id: int, body: MemberUpdate, user: User = Depends(auth.require_trainer),
                  session: Session = Depends(get_session)):
    member = session.get(User, member_id)
    if not member or member.team_id != user.team_id:
        raise HTTPException(404, "メンバーが見つかりません")
    team = session.get(Team, user.team_id)
    role = body.role if body.role is not None else member.role
    active = body.active if body.active is not None else member.active
    if role not in ("member", "trainer"):
        raise HTTPException(400, "役割が 正しくありません")
    if member.id == user.id and (role != "trainer" or not active):
        raise HTTPException(400, "自分の役割は 変えられません（ほかのトレーナーに頼んでね）")
    # 上限：これから入る枠（役割が変わる・使えるようになる）に 空きがあるか
    if active and (role != member.role or not member.active):
        limit = team.max_trainers if role == "trainer" else team.max_members
        if count_role(session, team.id, role) >= limit:
            raise HTTPException(409, "トレーナーの人数が 上限です" if role == "trainer" else "選手の人数が 上限です")
    member.role, member.active = role, active
    if body.position is not None:
        positions = services.positions_for(team.sport)
        if body.position and body.position not in positions:
            raise HTTPException(400, "ポジションを選んでね")
        member.position = body.position
    temporary = None
    if body.reset_password:  # 仮のパスワードを出す（前のログインは 使えなくなる）
        temporary = secrets.token_urlsafe(8)
        member.password_hash = auth.hash_password(temporary)
    session.commit()
    return {"ok": True, "temporary_password": temporary}


@router.get("/quick-replies")
def quick_replies(user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    rows = session.exec(select(QuickReply).where(QuickReply.user_id == user.id).order_by(QuickReply.id)).all()
    return [{"id": r.id, "body": r.body} for r in rows]


@router.post("/quick-replies")
def add_quick_reply(body: QuickReplyCreate, user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    text = body.body.strip()
    if not text or len(text) > config.QUICK_REPLY_BODY_MAX:
        raise HTTPException(400, f"1〜{config.QUICK_REPLY_BODY_MAX}文字で入れてね")
    count = session.exec(select(func.count()).select_from(QuickReply).where(QuickReply.user_id == user.id)).one()
    if count >= config.QUICK_REPLY_MAX:
        raise HTTPException(409, f"登録できるのは {config.QUICK_REPLY_MAX}個までです")
    session.add(QuickReply(user_id=user.id, body=text))
    session.commit()
    return {"ok": True}


@router.delete("/quick-replies/{reply_id}")
def delete_quick_reply(reply_id: int, user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    row = session.get(QuickReply, reply_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "見つかりません")
    session.delete(row)
    session.commit()
    return {"ok": True}


@router.patch("/team")
def update_team(body: TeamUpdate, user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    """チームの競技を変える（競技の欄に出る 用意した種目が 入れかわる。チームで足した種目は 残る）。"""
    if body.sport not in config.SPORTS:
        raise HTTPException(400, "競技を選んでね")
    team = session.get(Team, user.team_id)
    team.sport = body.sport
    session.commit()
    return {"ok": True}


# ---- 種目の名前の直し・まとめ ----

def team_exercise(session: Session, user: User, exercise_id: int) -> Exercise:
    """チームで足した種目（最初から用意した種目は 全チーム共通なので 直せない）。"""
    exercise = session.get(Exercise, exercise_id)
    if not exercise or exercise.team_id != user.team_id:
        raise HTTPException(404, "チームで足した種目だけ 直せます")
    return exercise


@router.get("/exercises")
def team_exercises(user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    """チームで足した種目と、それぞれの記録の数。"""
    rows = session.exec(select(Exercise).where(Exercise.team_id == user.team_id).order_by(Exercise.part, Exercise.id)).all()
    counts = dict(session.exec(select(WorkoutSet.exercise_id, func.count()).where(
        WorkoutSet.exercise_id.in_([e.id for e in rows])).group_by(WorkoutSet.exercise_id)).all()) if rows else {}
    return [{"id": e.id, "name": e.name, "part": e.part, "kind": services.kind_of(e), "sets": counts.get(e.id, 0)} for e in rows]


@router.patch("/exercises/{exercise_id}")
def rename_exercise(exercise_id: int, body: ExerciseRename, user: User = Depends(auth.require_trainer),
                    session: Session = Depends(get_session)):
    exercise = team_exercise(session, user, exercise_id)
    name = body.name.strip()
    if not name or len(name) > config.EXERCISE_NAME_MAX:
        raise HTTPException(400, f"種目の名前は 1〜{config.EXERCISE_NAME_MAX}文字で入れてね")
    if any(e.name == name and e.id != exercise.id for e in services.exercise_map(session, user.team_id).values()):
        raise HTTPException(409, "その名前の種目は もうあります。まとめるときは「まとめる」を使ってね")
    exercise.name = name
    session.commit()
    return {"ok": True}


@router.post("/exercises/{exercise_id}/merge")
def merge_exercise(exercise_id: int, body: ExerciseMerge, user: User = Depends(auth.require_trainer),
                   session: Session = Depends(get_session)):
    """種目をまとめる：今までの記録・休憩の長さ・メニュー・メモを まとめ先に移して、元の種目は消す（元に戻せない）。"""
    source = team_exercise(session, user, exercise_id)
    target = services.exercise_map(session, user.team_id).get(body.into_id)
    if not target or target.id == source.id:
        raise HTTPException(404, "まとめ先の種目が見つかりません")
    if services.kind_of(source) != services.kind_of(target):
        raise HTTPException(400, "記録の仕方が ちがう種目どうしは まとめられません")
    sets = session.exec(select(WorkoutSet).where(WorkoutSet.exercise_id == source.id)).all()
    user_ids = sorted({w.user_id for w in session.exec(select(Workout).where(Workout.id.in_([s.workout_id for s in sets]))).all()}) if sets else []
    for s in sets:
        s.exercise_id = target.id
    for item in session.exec(select(MenuItem).where(MenuItem.exercise_id == source.id)).all():
        item.exercise_id = target.id
    for row in session.exec(select(UserExercise).where(UserExercise.exercise_id == source.id)).all():
        if not session.get(UserExercise, (row.user_id, target.id)):  # まとめ先の 休憩の長さを 決めていなければ 引きつぐ
            session.add(UserExercise(user_id=row.user_id, exercise_id=target.id, rest_seconds=row.rest_seconds))
        session.delete(row)
    for note in session.exec(select(ExerciseNote).where(ExerciseNote.exercise_id == source.id)).all():
        other = session.exec(select(ExerciseNote).where(ExerciseNote.workout_id == note.workout_id,
                                                        ExerciseNote.exercise_id == target.id)).first()
        if other:  # 同じ筋トレに 両方のメモがあれば つなげる
            other.body = f"{other.body} / {note.body}"[:config.EXERCISE_NOTE_MAX]
            session.delete(note)
        else:
            note.exercise_id = target.id
    session.flush()
    session.delete(source)
    session.flush()
    services.recompute_prs(session, user_ids, target)  # 記録が 1本になったので、自己ベストの印を 付け直す
    session.commit()
    return {"ok": True, "moved_sets": len(sets)}


# ---- 体重・体脂肪率を 書き出す（スプレッドシートに 書き写す用）----

EXPORT_PERIODS = {7: "この1週間", 30: "この1か月", 0: "全部"}


@router.get("/export/body.csv")
def export_body(days: int = 7, user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    """体重・体脂肪率の CSV。ポジションごとに「RB」のような 1行を入れて、その下に 選手ごとの記録を並べる。

    1行目：選手名, 記録した日付, 体重(kg), 体脂肪率(%)。その期間に 記録がない選手も 名前だけ 1行 出す（入れ忘れが わかるように）。
    Excel で 文字化けしないように、先頭に BOM を付ける。
    """
    import csv
    import io
    from datetime import timedelta
    from urllib.parse import quote

    from fastapi import Response

    from app.models import BodyLog

    if days not in EXPORT_PERIODS:
        raise HTTPException(400, "期間を選んでね")
    team = session.get(Team, user.team_id)
    positions = services.positions_for(team.sport)
    players = [u for u in services.team_users(session, user.team_id).values() if u.active and u.role == "member"]
    query = select(BodyLog).where(BodyLog.user_id.in_([u.id for u in players])) if players else None
    if query is not None and days:
        query = query.where(BodyLog.day >= clock.today() - timedelta(days=days - 1))
    logs = session.exec(query.order_by(BodyLog.day)).all() if query is not None else []

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["選手名", "記録した日付", "体重(kg)", "体脂肪率(%)"])
    groups = [(p, [u for u in players if u.position == p]) for p in positions]
    groups.append(("未設定" if positions else "選手", [u for u in players if u.position not in positions]))
    for label, members in groups:
        if not members:
            continue
        writer.writerow([label])  # ポジションの 切りかわり
        for member in sorted(members, key=lambda u: u.name):
            mine = [log for log in logs if log.user_id == member.id]
            if not mine:
                writer.writerow([member.name, "", "", ""])
            for log in mine:
                writer.writerow([member.name, log.day.strftime("%Y/%m/%d"),
                                 f"{log.weight:g}" if log.weight is not None else "", f"{log.body_fat:g}" if log.body_fat is not None else ""])
    filename = f"体重_{EXPORT_PERIODS[days]}_{clock.today().isoformat()}.csv"
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})
