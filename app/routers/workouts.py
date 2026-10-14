"""種目と 筋トレの記録。どれも「ログインしている人の分だけ」をあつかう。"""

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app import auth, clock, config, push, rules, services
from app.db import get_session
from app.models import Exercise, ExerciseNote, Menu, Post, User, UserExercise, Workout, WorkoutSet
from app.routers.body import save_body

router = APIRouter(prefix="/api")


class ExerciseCreate(BaseModel):
    name: str
    part: str
    kind: str | None = None  # 記録の仕方（入れなければ 有酸素は 時間・距離、ほかは 重さ×回数）


class RestUpdate(BaseModel):
    seconds: int


class StartInput(BaseModel):
    body_weight: float | None = None  # 筋トレ前の体重（kg）
    day: date | None = None  # あとから記録：いつの分か（入れなければ 今から始める）
    minutes: int | None = None  # あとから記録：筋トレの長さ（分）
    menu_id: int | None = None  # トレーナーのメニューで始める


class SetValues(BaseModel):
    weight: float | None = None
    reps: int | None = None  # 回数（距離×本数なら 本数）
    seconds: int | None = None
    distance_m: float | None = None
    duration_min: float | None = None
    distance_km: float | None = None


class SetCreate(SetValues):
    exercise_id: int


class NoteInput(BaseModel):
    body: str


def workout_dict(session: Session, workout: Workout, exercises: dict[int, Exercise]) -> dict:
    sets = session.exec(select(WorkoutSet).where(WorkoutSet.workout_id == workout.id)).all()
    menu = session.get(Menu, workout.menu_id) if workout.menu_id else None
    notes = services.notes_by_workout(session, [workout.id])[workout.id]
    return {
        "id": workout.id,
        "day": workout.day.isoformat(),
        "started_at": workout.started_at.isoformat(),
        "status": workout.status,
        "minutes": workout.minutes,
        "backfill": workout.planned_minutes is not None,  # あとから記録（休憩タイマーは 動かさない）
        "planned_minutes": workout.planned_minutes,
        "posted": session.exec(select(Post.id).where(Post.workout_id == workout.id)).first() is not None,
        "menu": services.menu_dicts(session, [menu], exercises)[0] if menu else None,
        "exercises": services.summarize(sets, exercises, notes),
    }


def own_workout(session: Session, user: User, workout_id: int) -> Workout:
    workout = session.get(Workout, workout_id)
    if not workout or workout.user_id != user.id:
        raise HTTPException(404, "筋トレが見つかりません")
    return workout


def editable(session: Session, workout: Workout) -> None:
    """セットを直せるのは、記録中か、終わってから 投稿する前まで。"""
    if workout.status != "active" and session.exec(select(Post.id).where(Post.workout_id == workout.id)).first():
        raise HTTPException(409, "投稿した筋トレの セットは 直せません")


def make_values(kind: str, body: SetValues) -> dict:
    """記録の仕方に合わせて、入れた数字を確かめる。WorkoutSet に入れる値を返す。"""
    def check(value, low, high, message):
        if value is None or not low <= value <= high:
            raise HTTPException(400, message)
        return value

    if kind == "cardio":
        if not (body.duration_min or body.distance_km):
            raise HTTPException(400, "時間か 距離を入れてね")
        if not 0 <= (body.duration_min or 0) <= 600 or not 0 <= (body.distance_km or 0) <= 300:
            raise HTTPException(400, "数字を確かめてね")
        return {"duration_min": body.duration_min or None, "distance_km": body.distance_km or None}
    if kind == "time":
        return {"seconds": check(body.seconds, 1, 3600, "時間（秒）を入れてね")}
    if kind == "minutes":
        return {"duration_min": check(body.duration_min, 1, 600, "時間（分）を入れてね")}
    if kind == "count":
        return {"reps": check(body.reps, 1, 999, "本数を入れてね")}
    if kind == "distance_sets":
        return {"distance_m": check(body.distance_m, 1, 5000, "距離（m）を入れてね"), "reps": check(body.reps, 1, 99, "本数を入れてね")}
    if kind == "reps":
        return {"reps": check(body.reps, 1, 999, "回数を入れてね")}
    if body.weight is not None and not 0 <= body.weight <= 999:
        raise HTTPException(400, "重さを確かめてね")
    return {"weight": body.weight or None, "reps": check(body.reps, 1, 999, "回数を入れてね")}


def last_values(s: WorkoutSet, kind: str) -> dict:
    return {"weight": s.weight, "reps": s.reps, "seconds": s.seconds, "distance_m": s.distance_m,
            "duration_min": s.duration_min, "distance_km": s.distance_km, "label": rules.set_label(s, kind)}


@router.get("/exercises")
def list_exercises(user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """種目の一覧。自分の休憩の長さと、前回の記録も付ける。"""
    exercises = services.exercise_map(session, user.team_id)
    rests = {r.exercise_id: r.rest_seconds for r in session.exec(select(UserExercise).where(UserExercise.user_id == user.id)).all()}

    # 前回：その種目をやった いちばん新しい「終わった筋トレ」のセット
    since = clock.today() - timedelta(days=config.GRAPH_DAYS)
    rows = session.exec(select(WorkoutSet, Workout.id).join(Workout, Workout.id == WorkoutSet.workout_id).where(
        Workout.user_id == user.id, Workout.status == "done", Workout.day >= since,
    ).order_by(Workout.started_at.desc(), WorkoutSet.id)).all()
    last: dict[int, tuple[int, list[WorkoutSet]]] = {}
    for s, workout_id in rows:
        if s.exercise_id not in last:
            last[s.exercise_id] = (workout_id, [])
        if last[s.exercise_id][0] == workout_id:
            last[s.exercise_id][1].append(s)

    order = list(config.BODY_PARTS) + [config.SPORT_PART]
    items = sorted(exercises.values(), key=lambda e: (order.index(e.part) if e.part in order else 99, not e.preset, e.id))
    return {
        "rest": {"step": config.REST_STEP_SECONDS, "min": config.REST_MIN_SECONDS, "max": config.REST_MAX_SECONDS},
        "exercises": [{
            "id": e.id,
            "name": e.name,
            "part": e.part,
            "kind": services.kind_of(e),
            "cardio": services.kind_of(e) == "cardio",
            "preset": e.preset,
            "rest_seconds": rests.get(e.id) or services.default_rest(e),
            "last": [last_values(s, services.kind_of(e)) for s in last.get(e.id, (0, []))[1]],
        } for e in items],
    }


@router.post("/exercises")
def create_exercise(body: ExerciseCreate, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """種目を足す（チームのみんなが使える）。"""
    name = body.name.strip()
    if not name or len(name) > config.EXERCISE_NAME_MAX:
        raise HTTPException(400, f"種目の名前は 1〜{config.EXERCISE_NAME_MAX}文字で入れてね")
    if body.part not in config.BODY_PARTS and not (body.part == config.SPORT_PART and services.team_sport(session, user.team_id)):
        raise HTTPException(400, "部位を選んでね")
    if body.part == "cardio":
        kind = "cardio"
    else:
        kind = body.kind or "weight_reps"
        if kind not in config.RECORD_KINDS or kind == "cardio":
            raise HTTPException(400, "記録の仕方を選んでね")
    if any(e.name == name for e in services.exercise_map(session, user.team_id).values()):
        raise HTTPException(409, "その種目は もうあります")
    exercise = Exercise(team_id=user.team_id, name=name, part=body.part, kind=kind, created_by=user.id)
    session.add(exercise)
    session.commit()
    return {"id": exercise.id}


@router.put("/exercises/{exercise_id}/rest")
def set_rest(exercise_id: int, body: RestUpdate, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """この種目の 自分の休憩の長さを覚える。"""
    if exercise_id not in services.exercise_map(session, user.team_id):
        raise HTTPException(404, "種目が見つかりません")
    seconds = max(config.REST_MIN_SECONDS, min(config.REST_MAX_SECONDS, body.seconds))
    row = session.get(UserExercise, (user.id, exercise_id))
    if row:
        row.rest_seconds = seconds
    else:
        session.add(UserExercise(user_id=user.id, exercise_id=exercise_id, rest_seconds=seconds))
    session.commit()
    return {"seconds": seconds}


def active_workout(session: Session, user: User) -> Workout | None:
    return session.exec(select(Workout).where(Workout.user_id == user.id, Workout.status == "active")).first()


@router.get("/workouts/active")
def get_active(user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    workout = active_workout(session, user)
    if not workout:
        return {"workout": None}
    return {"workout": workout_dict(session, workout, services.exercise_map(session, user.team_id))}


@router.get("/workouts/{workout_id}")
def get_workout(workout_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    workout = own_workout(session, user, workout_id)
    return {"workout": workout_dict(session, workout, services.exercise_map(session, user.team_id))}


@router.post("/workouts/start")
def start(body: StartInput | None = None, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """筋トレを始める。体重を入れていれば、今日の体重として記録する（入れなくても 始められる）。

    day を入れると「あとから記録」：その日の分として、自分で入れた長さ（分）で記録する。
    """
    workout = active_workout(session, user)
    if not workout:
        body = body or StartInput()
        now = clock.now()
        workout = Workout(team_id=user.team_id, user_id=user.id, day=clock.day_of(now), started_at=now)
        if body.day is not None:
            today = clock.today()
            if not today - timedelta(days=config.WORKOUT_BACKFILL_DAYS) <= body.day <= today:
                raise HTTPException(400, f"あとから記録できるのは {config.WORKOUT_BACKFILL_DAYS}日前までです")
            minutes = body.minutes or config.BACKFILL_DEFAULT_MINUTES
            if not 1 <= minutes <= config.WORKOUT_MAX_MINUTES:
                raise HTTPException(400, f"時間は 1〜{config.WORKOUT_MAX_MINUTES}分で入れてね")
            workout.day, workout.planned_minutes = body.day, minutes
            # 今日の分は「今から その長さだけ前」、前の日の分は その日の昼に始めたことにする
            workout.started_at = now - timedelta(minutes=minutes) if body.day == today else datetime.combine(body.day, time(12))
        if body.menu_id is not None:
            menu = session.get(Menu, body.menu_id)
            targets = services.menu_targets(session, [body.menu_id])
            if not menu or menu.team_id != user.team_id or not menu.active or not services.menu_for(menu, user, targets):
                raise HTTPException(404, "メニューが見つかりません")
            workout.menu_id = menu.id
        if body.body_weight is not None and body.day is None:  # 体重は「今日の分」なので、あとから記録では 入れない
            save_body(session, user, body.body_weight, None)
        session.add(workout)
        session.commit()
    return {"workout": workout_dict(session, workout, services.exercise_map(session, user.team_id))}


def is_new_best(session: Session, user: User, target: WorkoutSet, kind: str) -> bool:
    """自己ベスト：この種目の ほかのどのセットよりも強い（初めての種目は 比べる相手がないので 付けない）。"""
    if kind == "cardio":
        return False
    others = [s for s in session.exec(select(WorkoutSet).join(Workout, Workout.id == WorkoutSet.workout_id).where(
        Workout.user_id == user.id, WorkoutSet.exercise_id == target.exercise_id)).all() if s.id != target.id]
    return bool(others) and rules.score(target, kind) > max(rules.score(s, kind) for s in others)


@router.post("/workouts/{workout_id}/sets")
def add_set(workout_id: int, body: SetCreate, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    workout = own_workout(session, user, workout_id)
    if workout.status != "active":
        raise HTTPException(409, "この筋トレは もう終わっています")
    exercises = services.exercise_map(session, user.team_id)
    exercise = exercises.get(body.exercise_id)
    if not exercise:
        raise HTTPException(404, "種目が見つかりません")
    kind = services.kind_of(exercise)
    new = WorkoutSet(workout_id=workout.id, exercise_id=exercise.id, **make_values(kind, body))
    new.is_pr = is_new_best(session, user, new, kind)
    session.add(new)
    session.commit()
    return {"workout": workout_dict(session, workout, exercises), "is_pr": new.is_pr}


@router.patch("/workouts/sets/{set_id}")
def update_set(set_id: int, body: SetValues, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """セットの数字を直す（記録中か、終わってから 投稿する前まで）。"""
    row = session.get(WorkoutSet, set_id)
    workout = own_workout(session, user, row.workout_id) if row else None
    if not workout:
        raise HTTPException(404, "セットが見つかりません")
    editable(session, workout)
    exercises = services.exercise_map(session, user.team_id)
    exercise = exercises.get(row.exercise_id)
    kind = services.kind_of(exercise)
    for field in ("weight", "reps", "seconds", "distance_m", "duration_min", "distance_km"):
        setattr(row, field, None)
    for field, value in make_values(kind, body).items():
        setattr(row, field, value)
    session.flush()
    if exercise:
        services.recompute_prs(session, [user.id], exercise)  # 数字が変わったので、自己ベストの印を 付け直す
    session.commit()
    return {"workout": workout_dict(session, workout, exercises)}


@router.delete("/workouts/sets/{set_id}")
def delete_set(set_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    row = session.get(WorkoutSet, set_id)
    workout = own_workout(session, user, row.workout_id) if row else None
    if not workout:
        raise HTTPException(404, "セットが見つかりません")
    editable(session, workout)
    exercise_id = row.exercise_id
    session.delete(row)
    session.flush()
    if not session.exec(select(WorkoutSet).where(WorkoutSet.workout_id == workout.id, WorkoutSet.exercise_id == exercise_id)).first():
        for note in session.exec(select(ExerciseNote).where(ExerciseNote.workout_id == workout.id, ExerciseNote.exercise_id == exercise_id)).all():
            session.delete(note)  # その種目のセットが なくなったら、メモも消す
    session.commit()
    return {"workout": workout_dict(session, workout, services.exercise_map(session, user.team_id))}


@router.put("/workouts/{workout_id}/notes/{exercise_id}")
def set_note(workout_id: int, exercise_id: int, body: NoteInput, user: User = Depends(auth.current_user),
             session: Session = Depends(get_session)):
    """種目ごとのメモ（空にすると 消える）。投稿したあとでも 自分の筋トレなら 直せる。"""
    workout = own_workout(session, user, workout_id)
    if not session.exec(select(WorkoutSet).where(WorkoutSet.workout_id == workout.id, WorkoutSet.exercise_id == exercise_id)).first():
        raise HTTPException(404, "この筋トレに その種目は ありません")
    text = body.body.strip()
    if len(text) > config.EXERCISE_NOTE_MAX:
        raise HTTPException(400, f"メモは {config.EXERCISE_NOTE_MAX}文字までです")
    note = session.exec(select(ExerciseNote).where(ExerciseNote.workout_id == workout.id, ExerciseNote.exercise_id == exercise_id)).first()
    if not text:
        if note:
            session.delete(note)
    elif note:
        note.body = text
    else:
        session.add(ExerciseNote(workout_id=workout.id, exercise_id=exercise_id, body=text))
    session.commit()
    return {"workout": workout_dict(session, workout, services.exercise_map(session, user.team_id))}


@router.post("/workouts/{workout_id}/finish")
def finish(workout_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    workout = own_workout(session, user, workout_id)
    if workout.status != "active":
        raise HTTPException(409, "この筋トレは もう終わっています")
    if not session.exec(select(WorkoutSet).where(WorkoutSet.workout_id == workout.id)).first():
        raise HTTPException(400, "セットが 1つもありません。やめるときは「取り消す」を押してね")
    if workout.planned_minutes is not None:  # あとから記録：自分で入れた長さを使う
        workout.minutes = workout.planned_minutes
        workout.ended_at = workout.started_at + timedelta(minutes=workout.planned_minutes)
    else:
        workout.ended_at = clock.now()
        minutes = int((workout.ended_at - workout.started_at).total_seconds() // 60)
        workout.minutes = max(1, min(config.WORKOUT_MAX_MINUTES, minutes))
    workout.status = "done"
    session.commit()
    push.cancel_rest(user.id)
    return {"workout": workout_dict(session, workout, services.exercise_map(session, user.team_id))}


@router.delete("/workouts/{workout_id}")
def delete_workout(workout_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """筋トレを取り消す（投稿していれば 投稿も消える）。"""
    workout = own_workout(session, user, workout_id)
    post = session.exec(select(Post).where(Post.workout_id == workout.id)).first()
    if post:
        services.delete_post(session, post)
    for model in (WorkoutSet, ExerciseNote):
        for row in session.exec(select(model).where(model.workout_id == workout.id)).all():
            session.delete(row)
    session.flush()
    session.delete(workout)
    session.commit()
    push.cancel_rest(user.id)
    return {"ok": True}
