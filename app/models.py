from datetime import date, datetime

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel, UniqueConstraint

from app import clock, config


class Team(SQLModel, table=True):
    """チーム。ほかのチームの記録は 見えない"""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    invite_code: str = Field(unique=True, index=True)  # 招待の合言葉
    max_members: int = config.TEAM_MAX_MEMBERS  # 選手の上限
    max_trainers: int = config.TEAM_MAX_TRAINERS  # トレーナーの上限
    sport: str = ""  # 競技（config.SPORTS のキー）。空なら 決めていない
    created_at: NaiveDatetime = Field(default_factory=clock.now)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    team_id: int = Field(foreign_key="team.id", index=True)
    login_id: str = Field(unique=True, index=True)  # アプリ全体で 1つだけ
    name: str
    password_hash: str
    role: str = "member"  # member（選手） / trainer
    active: bool = True  # False：やめた人（記録は残して、ログインだけできなくする）
    created_at: NaiveDatetime = Field(default_factory=clock.now)


class Exercise(SQLModel, table=True):
    """筋トレの種目。team_id が空なら 最初から用意した種目（全チーム共通）"""

    id: int | None = Field(default=None, primary_key=True)
    team_id: int | None = Field(default=None, foreign_key="team.id", index=True)
    name: str
    part: str  # config.BODY_PARTS のキー（cardio なら 有酸素）。sport なら 競技の欄
    kind: str = "weight_reps"  # 記録の仕方（config.RECORD_KINDS のキー）
    sport: str | None = None  # 競技向けに 用意した種目なら その競技（その競技のチームにだけ出る）
    preset: bool = False
    created_by: int | None = Field(default=None, foreign_key="users.id")
    created_at: NaiveDatetime = Field(default_factory=clock.now)


class UserExercise(SQLModel, table=True):
    """その人の その種目の設定（休憩の長さは 人によってちがう）"""

    user_id: int = Field(foreign_key="users.id", primary_key=True)
    exercise_id: int = Field(foreign_key="exercise.id", primary_key=True)
    rest_seconds: int


class Workout(SQLModel, table=True):
    """1回の筋トレ"""

    id: int | None = Field(default=None, primary_key=True)
    team_id: int = Field(foreign_key="team.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    day: date
    started_at: NaiveDatetime
    ended_at: NaiveDatetime | None = None
    status: str = "active"  # active（記録中） / done
    minutes: int | None = None
    planned_minutes: int | None = None  # あとから記録：自分で入れた 筋トレの長さ（分）。空なら ふつうの記録
    menu_id: int | None = Field(default=None, foreign_key="menu.id")  # トレーナーのメニューで始めたとき


class WorkoutSet(SQLModel, table=True):
    """1セット（有酸素なら 1回ぶん）"""

    id: int | None = Field(default=None, primary_key=True)
    workout_id: int = Field(foreign_key="workout.id", index=True)
    exercise_id: int = Field(foreign_key="exercise.id", index=True)
    weight: float | None = None  # kg（自重なら なし）
    reps: int | None = None
    duration_min: float | None = None  # 有酸素：時間（分）
    distance_km: float | None = None  # 有酸素：距離（km）
    seconds: int | None = None  # 時間（秒）の種目：プランクなど
    distance_m: float | None = None  # 距離×本数の種目：1本の距離（m）。本数は reps
    is_pr: bool = False  # 自己ベスト更新
    created_at: NaiveDatetime = Field(default_factory=clock.now)


class Post(SQLModel, table=True):
    """タイムラインの投稿（1つの筋トレに 1つ）"""

    id: int | None = Field(default=None, primary_key=True)
    team_id: int = Field(foreign_key="team.id", index=True)
    workout_id: int = Field(foreign_key="workout.id", unique=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    body: str = ""
    reviewed_by: int | None = Field(default=None, foreign_key="users.id")  # 見たトレーナー
    reviewed_at: NaiveDatetime | None = None  # 空なら「トレーナーが まだ見ていない」
    created_at: NaiveDatetime = Field(default_factory=clock.now)


class PostPhoto(SQLModel, table=True):
    """投稿の写真（写真そのものは 置き場にある。ここには場所だけ）"""

    id: int | None = Field(default=None, primary_key=True)
    post_id: int = Field(foreign_key="post.id", index=True)
    storage_key: str
    position: int = 0


class Comment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    post_id: int = Field(foreign_key="post.id", index=True)
    user_id: int = Field(foreign_key="users.id")
    parent_id: int | None = Field(default=None, foreign_key="comment.id")  # 返信なら 元のコメント（返信への返信も 同じ元に付ける）
    body: str
    created_at: NaiveDatetime = Field(default_factory=clock.now)


class Reaction(SQLModel, table=True):
    """スタンプ。投稿か コメントの どちらかに付く（1人が 1つの相手に 1つ）"""

    __table_args__ = (UniqueConstraint("user_id", "post_id", "comment_id"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    post_id: int | None = Field(default=None, foreign_key="post.id", index=True)
    comment_id: int | None = Field(default=None, foreign_key="comment.id", index=True)
    emoji: str
    created_at: NaiveDatetime = Field(default_factory=clock.now)


class Notice(SQLModel, table=True):
    """ベルの知らせ"""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)  # 受け取る人
    kind: str  # comment / trainer_comment / reply / reaction
    post_id: int = Field(foreign_key="post.id", index=True)
    actor_id: int = Field(foreign_key="users.id")  # した人
    read_at: NaiveDatetime | None = None
    created_at: NaiveDatetime = Field(default_factory=clock.now)


class QuickReply(SQLModel, table=True):
    """トレーナーの よく使う言葉"""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    body: str


class PushSub(SQLModel, table=True):
    """プッシュ通知の送り先（スマホ・ブラウザごとに 1つ）"""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    endpoint: str = Field(unique=True)
    p256dh: str
    auth: str
    created_at: NaiveDatetime = Field(default_factory=clock.now)


class BodyLog(SQLModel, table=True):
    """体重・体脂肪率の記録（1人 1日に 1つ。自分とトレーナーだけに見える）"""

    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    day: date
    weight: float | None = None  # kg
    body_fat: float | None = None  # %
    created_at: NaiveDatetime = Field(default_factory=clock.now)


class Menu(SQLModel, table=True):
    """トレーナーが配る メニュー（チームのみんなが それを開いて 記録できる）"""

    id: int | None = Field(default=None, primary_key=True)
    team_id: int = Field(foreign_key="team.id", index=True)
    title: str
    note: str = ""  # トレーナーからのコメント
    start_day: date | None = None  # いつから（空なら 決めていない）
    end_day: date | None = None  # いつまで（すぎたら みんなの画面から消える）
    created_by: int = Field(foreign_key="users.id")
    active: bool = True  # False：配るのをやめた（そのメニューでやった記録は 残る）
    created_at: NaiveDatetime = Field(default_factory=clock.now)


class MenuItem(SQLModel, table=True):
    """メニューの 1種目（目標の重さ・回数・セット数）"""

    id: int | None = Field(default=None, primary_key=True)
    menu_id: int = Field(foreign_key="menu.id", index=True)
    exercise_id: int = Field(foreign_key="exercise.id")
    sets: int
    reps: int  # 回数（距離×本数なら 本数）。使わない記録の仕方では 0
    weight: float | None = None  # 目標の重さ（kg）。空なら 自分で決める
    seconds: int | None = None  # 時間（秒）の種目の目標
    distance_m: float | None = None  # 距離×本数の種目の 1本の距離（m）
    minutes: float | None = None  # 有酸素の目標の時間（分）
    distance_km: float | None = None  # 有酸素の目標の距離（km）
    position: int = 0


class MenuPhoto(SQLModel, table=True):
    """メニューの写真（トレーナーが 紙やスプレッドシートの写真で配るとき）"""

    id: int | None = Field(default=None, primary_key=True)
    menu_id: int = Field(foreign_key="menu.id", index=True)
    storage_key: str
    position: int = 0


class MenuTarget(SQLModel, table=True):
    """メニューを配る相手（1人も入っていなければ チーム全員）"""

    menu_id: int = Field(foreign_key="menu.id", primary_key=True)
    user_id: int = Field(foreign_key="users.id", primary_key=True)


class ExerciseNote(SQLModel, table=True):
    """種目ごとのメモ（1回の筋トレの 1つの種目に 1つ。付けたいときだけ）"""

    __table_args__ = (UniqueConstraint("workout_id", "exercise_id"),)

    id: int | None = Field(default=None, primary_key=True)
    workout_id: int = Field(foreign_key="workout.id", index=True)
    exercise_id: int = Field(foreign_key="exercise.id")
    body: str
