"""データベース。DATABASE_URL があれば それ（Neon の PostgreSQL）、なければ パソコンの中のファイル。"""

import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

DATA_DIR = Path(os.getenv("APP_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        return f"sqlite:///{DATA_DIR / 'app.db'}"
    # Neon などが出す postgres:// / postgresql:// を、使うドライバ（psycopg）の書き方にそろえる
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


URL = database_url()
engine = create_engine(URL, connect_args={"check_same_thread": False} if URL.startswith("sqlite") else {},
                       pool_pre_ping=True)


def seed_presets(session: Session) -> None:
    """最初から用意する種目を入れる（もうあるものは、記録の仕方だけ 合わせる）。"""
    from app import config
    from app.models import Exercise

    existing = {e.name: e for e in session.exec(select(Exercise).where(Exercise.team_id == None)).all()}  # noqa: E711
    wanted = []  # (名前, 部位, 記録の仕方, 競技)
    for part, names in config.PRESET_EXERCISES.items():
        for name in names:
            wanted.append((name, part, "cardio" if part == "cardio" else config.PRESET_KINDS.get(name, "weight_reps"), None))
    for sport, items in config.SPORT_EXERCISES.items():
        for name, kind in items:
            wanted.append((name, config.SPORT_PART, kind, sport))
    for name, part, kind, sport in wanted:
        exercise = existing.get(name)
        if exercise is None:
            session.add(Exercise(name=name, part=part, kind=kind, sport=sport, preset=True))
        elif exercise.kind != kind:  # 前の版で作った種目にも、記録の仕方を付ける
            exercise.kind = kind
    # 前の版で 競技の欄に入れていた種目（パワークリーンなど）を 片づける：
    # - だれも使っていなければ 消す
    # - 1つのチームだけが使っていたら、そのチームで足した種目にして、ふつうの部位の欄（脚など）へ移す（記録は そのまま）
    # - いくつものチームが使っていたら、記録が こわれないように そのままにしておく
    wanted_names = {w[0] for w in wanted}
    for name, exercise in existing.items():
        if not exercise.sport or name in wanted_names:
            continue
        teams = teams_using(session, exercise.id)
        if not teams:
            session.delete(exercise)
        elif len(teams) == 1:
            exercise.team_id, exercise.sport, exercise.preset = teams.pop(), None, False
            exercise.part = config.RETIRED_SPORT_PARTS.get(name, "legs")
    session.commit()


def teams_using(session: Session, exercise_id: int) -> set[int]:
    """その種目を 使っているチーム（記録・メニュー・休憩の長さ・メモ）。"""
    from app.models import ExerciseNote, Menu, MenuItem, User, UserExercise, Workout, WorkoutSet

    teams = set(session.exec(select(Workout.team_id).join(WorkoutSet, WorkoutSet.workout_id == Workout.id)
                             .where(WorkoutSet.exercise_id == exercise_id)).all())
    teams |= set(session.exec(select(Workout.team_id).join(ExerciseNote, ExerciseNote.workout_id == Workout.id)
                              .where(ExerciseNote.exercise_id == exercise_id)).all())
    teams |= set(session.exec(select(Menu.team_id).join(MenuItem, MenuItem.menu_id == Menu.id)
                              .where(MenuItem.exercise_id == exercise_id)).all())
    teams |= set(session.exec(select(User.team_id).join(UserExercise, UserExercise.user_id == User.id)
                              .where(UserExercise.exercise_id == exercise_id)).all())
    return teams


def sql_literal(value) -> str:
    """SQL の中に書く 値（最初の値に使う）。"""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def add_missing_columns() -> list[str]:
    """あとから足した項目（列）を、前からある表にも足す。足した列の名前を返す。

    create_all は 新しい表は作るが、前からある表に 列は足してくれないので、ここで足す（kintore_app と同じ作り）。
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateColumn

    added = []
    with engine.begin() as conn:
        inspector = inspect(conn)
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                definition = str(CreateColumn(column).compile(dialect=conn.dialect))
                if not column.nullable:  # 前からある行のために、決まった最初の値を入れる
                    default = column.default.arg if column.default is not None and not callable(column.default.arg) else None
                    if default is None:
                        raise RuntimeError(f"{table.name}.{column.name} は 最初の値が決まっていないので 足せません")
                    definition += f" DEFAULT {sql_literal(default)}"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN {definition}'))
                added.append(f"{table.name}.{column.name}")
    return added


def init_db() -> None:
    from app import models  # noqa: F401  テーブルを登録する

    add_missing_columns()  # 先に足す：前からある表に 新しい列がないと、下の片づけなどが 動かない
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_presets(session)
        from app import services

        services.expire_photos(session, force=True)  # 起動したときに 保存期間をすぎた写真を片づける


def get_session():
    with Session(engine) as session:
        yield session
