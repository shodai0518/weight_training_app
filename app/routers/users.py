"""チームを作る・入る・ログイン、メンバーのページ、知らせ、プッシュ通知の登録。"""

import hmac
import os
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app import auth, clock, config, push, rules, services
from app.db import get_session
from app.models import Notice, Post, PushSub, Reaction, Team, User, Workout, WorkoutSet
from app.routers.body import body_logs

router = APIRouter(prefix="/api")

LOGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{3,30}$")
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 見まちがえやすい文字（0 と O、1 と I）は入れない


def new_invite_code() -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))


def count_role(session: Session, team_id: int, role: str) -> int:
    return session.exec(select(func.count()).select_from(User).where(
        User.team_id == team_id, User.role == role, User.active == True)).one()  # noqa: E712


def check_account(session: Session, name: str, login_id: str, password: str) -> None:
    if not name.strip() or len(name.strip()) > config.NAME_MAX:
        raise HTTPException(400, f"名前は 1〜{config.NAME_MAX}文字で入れてね")
    if not LOGIN_ID_PATTERN.match(login_id):
        raise HTTPException(400, "ログインIDは 半角の英数字 3〜30文字で入れてね")
    if len(password) < config.PASSWORD_MIN:
        raise HTTPException(400, f"パスワードは {config.PASSWORD_MIN}文字以上にしてね")
    if session.exec(select(User).where(User.login_id == login_id)).first():
        raise HTTPException(409, "そのログインIDは もう使われています")


class TeamCreate(BaseModel):
    team_name: str
    sport: str = ""  # 競技（config.SPORTS のキー）
    name: str
    login_id: str
    password: str
    operator_code: str = ""


class PositionInput(BaseModel):
    position: str


class Register(BaseModel):
    invite_code: str
    name: str
    login_id: str
    password: str


class Login(BaseModel):
    login_id: str
    password: str


@router.get("/setup")
def setup():
    """ログインの前の画面で使う。"""
    return {"needs_operator_code": bool(os.getenv("OPERATOR_CODE")), "sports": config.SPORTS}


@router.post("/teams")
def create_team(body: TeamCreate, request: Request, response: Response, session: Session = Depends(get_session)):
    """チームを作る。作った人が 最初のトレーナーになる。"""
    expected = os.getenv("OPERATOR_CODE")
    if expected:
        client, recent = auth.check_lock(request)
        if not hmac.compare_digest(body.operator_code.encode(), expected.encode()):
            auth.note_failure(client, recent)
            raise HTTPException(403, "運営用の合言葉がちがいます")
    team_name = body.team_name.strip()
    if not team_name or len(team_name) > 30:
        raise HTTPException(400, "チームの名前は 1〜30文字で入れてね")
    if body.sport not in config.SPORTS:
        raise HTTPException(400, "競技を選んでね")
    check_account(session, body.name, body.login_id, body.password)
    team = Team(name=team_name, invite_code=new_invite_code(), sport=body.sport)
    session.add(team)
    session.flush()
    user = User(team_id=team.id, login_id=body.login_id, name=body.name.strip(),
                password_hash=auth.hash_password(body.password), role="trainer")
    session.add(user)
    session.commit()
    auth.set_login(request, response, user)
    return {"ok": True}


@router.post("/register")
def register(body: Register, request: Request, response: Response, session: Session = Depends(get_session)):
    """招待の合言葉で チームに入る（選手として）。"""
    client, recent = auth.check_lock(request)
    team = session.exec(select(Team).where(Team.invite_code == body.invite_code.strip().upper())).first()
    if not team:
        auth.note_failure(client, recent)
        raise HTTPException(404, "招待の合言葉がちがいます")
    check_account(session, body.name, body.login_id, body.password)
    if count_role(session, team.id, "member") >= team.max_members:
        raise HTTPException(409, "このチームは 人数がいっぱいです。トレーナーに伝えてね")
    user = User(team_id=team.id, login_id=body.login_id, name=body.name.strip(),
                password_hash=auth.hash_password(body.password))
    session.add(user)
    session.commit()
    auth.set_login(request, response, user)
    return {"ok": True}


@router.post("/login")
def login(body: Login, request: Request, response: Response, session: Session = Depends(get_session)):
    client, recent = auth.check_lock(request)
    user = session.exec(select(User).where(User.login_id == body.login_id.strip())).first()
    if not user or not user.active or not auth.verify_password(body.password, user.password_hash):
        auth.note_failure(client, recent)
        raise HTTPException(401, "ログインIDか パスワードがちがいます")
    auth.clear_failures(client)
    auth.set_login(request, response, user)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    auth.clear_login(response)
    return {"ok": True}


def unread_count(session: Session, user: User) -> int:
    return session.exec(select(func.count()).select_from(Notice).where(
        Notice.user_id == user.id, Notice.read_at == None)).one()  # noqa: E711


@router.get("/me")
def me(user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    team = session.get(Team, user.team_id)
    return {
        "user": {**services.user_brief(user), "login_id": user.login_id},  # position も入っている
        "team": {"id": team.id, "name": team.name, "sport": team.sport},
        "positions": services.positions_for(team.sport),
        "parts": team_parts(team),
        "kinds": config.RECORD_KINDS,
        "reactions": config.REACTIONS,
        "limits": {"photos": config.POST_MAX_PHOTOS, "body": config.POST_BODY_MAX, "comment": config.COMMENT_MAX,
                   "photo_keep_days": config.PHOTO_KEEP_DAYS},
        "vapid_key": push.public_key(),
        "unread": unread_count(session, user),
    }


def team_parts(team: Team) -> dict[str, str]:
    """記録の画面の 部位のボタン。競技を決めたチームは、最後に 競技のボタンが増える。"""
    if not team.sport:
        return config.BODY_PARTS
    return {**config.BODY_PARTS, config.SPORT_PART: config.SPORTS.get(team.sport, "競技")}


# ---- メンバー ----

def member_stats(session: Session, team_id: int) -> dict[int, dict]:
    """人ごとの 最後に筋トレした日・この7日の回数・最近の自己ベスト。"""
    today = clock.today()
    week_ago = today - timedelta(days=6)
    stats: dict[int, dict] = {}
    rows = session.exec(select(Workout.user_id, Workout.day).where(Workout.team_id == team_id, Workout.status == "done")).all()
    for user_id, day in rows:
        entry = stats.setdefault(user_id, {"last_day": None, "week_count": 0, "recent_pr": False})
        if entry["last_day"] is None or day > entry["last_day"]:
            entry["last_day"] = day
        if day >= week_ago:
            entry["week_count"] += 1
    pr_users = session.exec(select(Workout.user_id).join(WorkoutSet, WorkoutSet.workout_id == Workout.id).where(
        Workout.team_id == team_id, Workout.day >= week_ago, WorkoutSet.is_pr == True).distinct()).all()  # noqa: E712
    for user_id in pr_users:
        stats.setdefault(user_id, {"last_day": None, "week_count": 0, "recent_pr": False})["recent_pr"] = True
    return stats


def member_row(user: User, stats: dict[int, dict]) -> dict:
    entry = stats.get(user.id, {"last_day": None, "week_count": 0, "recent_pr": False})
    days_since = (clock.today() - entry["last_day"]).days if entry["last_day"] else None
    return {
        **services.user_brief(user),
        "last_day": entry["last_day"].isoformat() if entry["last_day"] else None,
        "days_since": days_since,
        "idle": days_since is None or days_since >= config.INACTIVE_DAYS,
        "week_count": entry["week_count"],
        "recent_pr": entry["recent_pr"],
    }


@router.put("/me/position")
def set_my_position(body: PositionInput, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """自分の ポジションを決める（最初に 聞かれる。あとから「自分」で 変えられる）。"""
    user.position = check_position(session, user.team_id, body.position)
    session.commit()
    return {"position": user.position}


def check_position(session: Session, team_id: int, position: str) -> str:
    positions = services.positions_for(services.team_sport(session, team_id))
    if position not in positions:
        raise HTTPException(400, "ポジションを選んでね")
    return position


@router.get("/members")
def members(user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    """メンバーの一覧（選手を ポジションの順に。トレーナーは 最後）。"""
    stats = member_stats(session, user.team_id)
    positions = services.positions_for(services.team_sport(session, user.team_id))
    by_position = services.position_order(positions)
    users = [u for u in services.team_users(session, user.team_id).values() if u.active]
    users.sort(key=lambda u: (u.role != "member", by_position(u)))
    return [member_row(u, stats) for u in users]


@router.get("/members/{member_id}")
def member_detail(member_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """その人の 筋トレの履歴と 種目ごとの伸び（同じチームなら だれでも見られる）。体重は 本人とトレーナーだけ。"""
    member = session.get(User, member_id)
    if not member or member.team_id != user.team_id:
        raise HTTPException(404, "メンバーが見つかりません")
    exercises = services.exercise_map(session, user.team_id)
    since = clock.today() - timedelta(days=config.GRAPH_DAYS)
    workouts = session.exec(select(Workout).where(
        Workout.user_id == member.id, Workout.status == "done", Workout.day >= since).order_by(Workout.started_at.desc())).all()
    sets = services.sets_by_workout(session, [w.id for w in workouts])
    notes = services.notes_by_workout(session, [w.id for w in workouts])
    posts = {}
    if workouts:
        posts = dict(session.exec(select(Post.workout_id, Post.id).where(Post.workout_id.in_([w.id for w in workouts]))).all())

    history_since = clock.today() - timedelta(days=config.HISTORY_DAYS)
    history = [{
        "id": w.id,
        "day": w.day.isoformat(),
        "minutes": w.minutes,
        "post_id": posts.get(w.id),
        "exercises": services.summarize(sets[w.id], exercises, notes[w.id]),
    } for w in workouts if w.day >= history_since]

    # 種目ごとの伸び：その日の いちばん強いセット（重さ×回数は 見つもり1RM、ほかは 回数・秒・距離）
    series: dict[int, dict] = {}
    for w in reversed(workouts):
        grouped: dict[int, list[WorkoutSet]] = {}
        for s in sets[w.id]:
            grouped.setdefault(s.exercise_id, []).append(s)
        for exercise_id, group in grouped.items():
            exercise = exercises.get(exercise_id)
            kind = services.kind_of(exercise)
            best = rules.best_set(group, kind)
            if not exercise or kind == "cardio" or not best:
                continue
            entry = series.setdefault(exercise_id, {"exercise_id": exercise_id, "name": exercise.name, "points": []})
            value, unit = rules.graph_point(best, kind)
            entry["points"].append({"day": w.day.isoformat(), "value": value, "unit": unit, "label": rules.set_label(best, kind)})
    return {
        "user": {**services.user_brief(member), "active": member.active},
        # 体重・体脂肪率は 本人とトレーナーにだけ渡す（ほかの人には None）
        "body": body_logs(session, member.id) if member.id == user.id or user.role == "trainer" else None,
        "history": history,
        "series": sorted(series.values(), key=lambda e: -len(e["points"])),
    }


# ---- 知らせ ----

NOTICE_TEXT = {
    "trainer_comment": "トレーナーの {name} さんが コメントしました",
    "comment": "{name} さんが コメントしました",
    "reply": "{name} さんが あなたのコメントに 返信しました",
}


def reaction_summary(session: Session, post_id: int, owner_id: int, users: dict[int, User]) -> tuple[str, list[dict]]:
    """スタンプの知らせ：「たなか さん、すずき さん ほか2人が スタンプを付けました 👍🔥」と、だれが どれを押したか。"""
    rows = session.exec(select(Reaction).where(Reaction.post_id == post_id, Reaction.user_id != owner_id)
                        .order_by(Reaction.created_at.desc(), Reaction.id.desc())).all()
    if not rows:
        return "スタンプは 外されました", []
    names = [services.user_brief(users.get(r.user_id))["name"] for r in rows]
    shown = "、".join(f"{n} さん" for n in names[:2])
    others = f" ほか{len(names) - 2}人" if len(names) > 2 else ""
    emojis = "".join(e for e in config.REACTIONS if any(r.emoji == e for r in rows))
    who = [{"name": n, "emoji": r.emoji} for n, r in zip(names, rows)]
    return f"{shown}{others}が スタンプを付けました {emojis}", who


@router.get("/notices")
def notices(user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    rows = session.exec(select(Notice).where(Notice.user_id == user.id).order_by(Notice.id.desc()).limit(config.NOTICE_LIST)).all()
    users = services.team_users(session, user.team_id)
    result = []
    for n in rows:
        item = {"id": n.id, "post_id": n.post_id, "kind": n.kind, "unread": n.read_at is None, "created_at": n.created_at.isoformat()}
        if n.kind == "reaction":
            item["text"], item["who"] = reaction_summary(session, n.post_id, user.id, users)
        else:
            item["text"] = NOTICE_TEXT.get(n.kind, "{name} さんから").format(name=services.user_brief(users.get(n.actor_id))["name"])
        result.append(item)
    return result


@router.get("/notices/count")
def notices_count(user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    return {"unread": unread_count(session, user)}


@router.post("/notices/read")
def notices_read(user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    for n in session.exec(select(Notice).where(Notice.user_id == user.id, Notice.read_at == None)).all():  # noqa: E711
        n.read_at = clock.now()
    session.commit()
    return {"ok": True}


# ---- プッシュ通知 ----

class Subscription(BaseModel):
    endpoint: str
    keys: dict[str, str]


class RestPush(BaseModel):
    seconds: float  # 休憩が終わるまでの 残り（秒）
    name: str = ""


@router.post("/push/subscribe")
def push_subscribe(body: Subscription, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    if not push.allowed_endpoint(body.endpoint) or "p256dh" not in body.keys or "auth" not in body.keys:
        raise HTTPException(400, "通知の登録が 正しくありません")
    sub = session.exec(select(PushSub).where(PushSub.endpoint == body.endpoint)).first()
    if sub:  # 同じスマホを 別の人が使い始めたら、その人のものにする
        sub.user_id, sub.p256dh, sub.auth = user.id, body.keys["p256dh"], body.keys["auth"]
    else:
        session.add(PushSub(user_id=user.id, endpoint=body.endpoint, p256dh=body.keys["p256dh"], auth=body.keys["auth"]))
    session.flush()
    # 1人の送り先が 多すぎたら、古いものから忘れる
    mine = session.exec(select(PushSub).where(PushSub.user_id == user.id).order_by(PushSub.id)).all()
    for old in mine[:-push.MAX_SUBSCRIPTIONS_PER_USER]:
        session.delete(old)
    session.commit()
    return {"ok": True}


@router.post("/push/unsubscribe")
def push_unsubscribe(body: Subscription, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    sub = session.exec(select(PushSub).where(PushSub.endpoint == body.endpoint, PushSub.user_id == user.id)).first()
    if sub:
        session.delete(sub)
        session.commit()
    return {"ok": True}


@router.post("/push/test")
def push_test(user: User = Depends(auth.current_user)):
    push.send(user.id, {"title": "通知のテスト", "body": "この通知が見えていれば、休憩の終わりも 届きます", "tag": "test"})
    return {"ok": True}


@router.post("/rest/push")
def rest_push(body: RestPush, user: User = Depends(auth.current_user)):
    """休憩が終わる時刻に「休憩おわり」を送るように 予約する。"""
    seconds = max(0.0, min(float(config.REST_MAX_SECONDS), body.seconds))
    name = body.name.strip()[:config.EXERCISE_NAME_MAX]
    push.schedule_rest(user.id, seconds, {
        "title": "休憩おわり！", "body": f"{name}：次のセットへ" if name else "次のセットへ", "tag": "rest",
    })
    return {"ok": True}


@router.delete("/rest/push")
def rest_push_cancel(user: User = Depends(auth.current_user)):
    push.cancel_rest(user.id)
    return {"ok": True}
