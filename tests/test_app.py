import base64

from sqlmodel import Session, select

from app import db, push
from app.models import Team, Workout
from tests.conftest import create_team, do_workout, exercise_id, join, make_client

JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 200).decode()


def me(client):
    return client.get("/api/me").json()["user"]


# ---- チームとアカウント ----

def test_create_team_makes_first_trainer():
    coach = create_team()
    data = coach.get("/api/me").json()
    assert data["user"]["role"] == "trainer"
    assert data["team"]["name"] == "レッドチーム"


def test_join_with_invite_code_as_member():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    assert me(player)["role"] == "member"
    assert [m["name"] for m in coach.get("/api/members").json()] == ["たなか", "コーチ"]


def test_wrong_invite_code_and_duplicate_login_id():
    coach = create_team()
    client = make_client()
    body = {"invite_code": "XXXXXXXX", "name": "a", "login_id": "someone", "password": "password123"}
    assert client.post("/api/register", json=body).status_code == 404
    code = coach.get("/api/admin/team").json()["invite_code"]
    assert client.post("/api/register", json={**body, "invite_code": code, "login_id": "coach"}).status_code == 409


def test_login_logout_and_wrong_password():
    create_team()
    client = make_client()
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/login", json={"login_id": "coach", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/login", json={"login_id": "coach", "password": "password123"}).status_code == 200
    assert client.get("/api/me").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_member_limit():
    coach = create_team()
    with Session(db.engine) as session:
        team = session.exec(select(Team)).one()
        team.max_members = 1
        session.commit()
    join(coach, "たなか", "tanaka")
    code = coach.get("/api/admin/team").json()["invite_code"]
    res = make_client().post("/api/register", json={"invite_code": code, "name": "すずき", "login_id": "suzuki", "password": "password123"})
    assert res.status_code == 409
    # やめた人を 使えなくすると 1人分あく
    tanaka = next(u for u in coach.get("/api/admin/team").json()["users"] if u["login_id"] == "tanaka")
    assert coach.patch(f"/api/admin/members/{tanaka['id']}", json={"active": False}).status_code == 200
    res = make_client().post("/api/register", json={"invite_code": code, "name": "すずき", "login_id": "suzuki", "password": "password123"})
    assert res.status_code == 200


def test_trainer_limit_and_role_change():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    player_id = me(player)["id"]
    with Session(db.engine) as session:
        team = session.exec(select(Team)).one()
        team.max_trainers = 1
        session.commit()
    assert coach.patch(f"/api/admin/members/{player_id}", json={"role": "trainer"}).status_code == 409
    with Session(db.engine) as session:
        team = session.exec(select(Team)).one()
        team.max_trainers = 2
        session.commit()
    assert coach.patch(f"/api/admin/members/{player_id}", json={"role": "trainer"}).status_code == 200
    assert me(player)["role"] == "trainer"
    # 自分の役割は 変えられない
    assert coach.patch(f"/api/admin/members/{me(coach)['id']}", json={"role": "member"}).status_code == 400


def test_deactivated_member_cannot_use_app():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    coach.patch(f"/api/admin/members/{me(player)['id']}", json={"active": False})
    assert player.get("/api/me").status_code == 401
    assert make_client().post("/api/login", json={"login_id": "tanaka", "password": "password123"}).status_code == 401


def test_reset_password_gives_temporary_password_and_ends_old_login():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    res = coach.patch(f"/api/admin/members/{me(player)['id']}", json={"reset_password": True}).json()
    assert player.get("/api/me").status_code == 401  # 前のログインは 使えない
    assert make_client().post("/api/login", json={"login_id": "tanaka", "password": res["temporary_password"]}).status_code == 200


def test_members_cannot_use_admin():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    assert player.get("/api/admin/inbox").status_code == 403
    assert player.get("/api/admin/team").status_code == 403
    assert player.post("/api/admin/invite").status_code == 403
    assert player.patch(f"/api/admin/members/{me(player)['id']}", json={"role": "trainer"}).status_code == 403


def test_operator_code_is_needed_when_set(monkeypatch):
    monkeypatch.setenv("OPERATOR_CODE", "secret-code")
    body = {"team_name": "A", "name": "a", "login_id": "aaa", "password": "password123", "sport": "football"}
    assert make_client().post("/api/teams", json=body).status_code == 403
    assert make_client().post("/api/teams", json={**body, "operator_code": "secret-code"}).status_code == 200


# ---- 筋トレの記録 ----

def test_workout_flow_and_summary():
    coach = create_team()
    workout = coach.post("/api/workouts/start").json()["workout"]
    bench = exercise_id(coach, "ベンチプレス")
    coach.post(f"/api/workouts/{workout['id']}/sets", json={"exercise_id": bench, "weight": 80, "reps": 8})
    res = coach.post(f"/api/workouts/{workout['id']}/sets", json={"exercise_id": bench, "weight": 82.5, "reps": 6}).json()
    entry = res["workout"]["exercises"][0]
    assert entry["count"] == 2
    assert [s["label"] for s in entry["sets"]] == ["80kg×8回", "82.5kg×6回"]
    done = coach.post(f"/api/workouts/{workout['id']}/finish").json()["workout"]
    assert done["status"] == "done"
    assert coach.get("/api/workouts/active").json()["workout"] is None


def test_start_twice_returns_same_workout_and_empty_cannot_finish():
    coach = create_team()
    first = coach.post("/api/workouts/start").json()["workout"]
    second = coach.post("/api/workouts/start").json()["workout"]
    assert first["id"] == second["id"]
    assert coach.post(f"/api/workouts/{first['id']}/finish").status_code == 400


def test_personal_record_is_marked_only_when_beating_history():
    coach = create_team()
    do_workout(coach, [("スクワット", 100, 5)])  # 初めては 印なし
    workout = coach.post("/api/workouts/start").json()["workout"]
    squat = exercise_id(coach, "スクワット")
    same = coach.post(f"/api/workouts/{workout['id']}/sets", json={"exercise_id": squat, "weight": 100, "reps": 5}).json()
    better = coach.post(f"/api/workouts/{workout['id']}/sets", json={"exercise_id": squat, "weight": 105, "reps": 5}).json()
    assert same["is_pr"] is False
    assert better["is_pr"] is True


def test_exercises_show_last_record_and_remember_rest_per_user():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    do_workout(coach, [("ベンチプレス", 80, 8), ("ベンチプレス", 80, 7)])
    bench = next(e for e in coach.get("/api/exercises").json()["exercises"] if e["name"] == "ベンチプレス")
    assert [s["label"] for s in bench["last"]] == ["80kg×8回", "80kg×7回"]
    assert bench["rest_seconds"] == 180  # ビッグ3
    coach.put(f"/api/exercises/{bench['id']}/rest", json={"seconds": 150})
    assert next(e for e in coach.get("/api/exercises").json()["exercises"] if e["id"] == bench["id"])["rest_seconds"] == 150
    other = next(e for e in player.get("/api/exercises").json()["exercises"] if e["id"] == bench["id"])
    assert other["rest_seconds"] == 180 and other["last"] == []


def test_cannot_touch_other_users_workout():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    workout = coach.post("/api/workouts/start").json()["workout"]
    bench = exercise_id(player, "ベンチプレス")
    assert player.post(f"/api/workouts/{workout['id']}/sets", json={"exercise_id": bench, "weight": 50, "reps": 5}).status_code == 404
    assert player.post(f"/api/workouts/{workout['id']}/finish").status_code == 404
    assert player.delete(f"/api/workouts/{workout['id']}").status_code == 404


def test_added_exercise_is_shared_in_team_only():
    red = create_team()
    red_player = join(red, "たなか", "tanaka")
    blue = create_team("ブルーチーム", "ブルーコーチ", "bluecoach")
    assert red.post("/api/exercises", json={"name": "ケーブルクロス", "part": "chest"}).status_code == 200
    assert red.post("/api/exercises", json={"name": "ケーブルクロス", "part": "chest"}).status_code == 409
    names = lambda c: [e["name"] for e in c.get("/api/exercises").json()["exercises"]]  # noqa: E731
    assert "ケーブルクロス" in names(red_player)
    assert "ケーブルクロス" not in names(blue)


# ---- 投稿・タイムライン ----

def test_post_with_photos_appears_on_timeline():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    workout_id = do_workout(player, [("ベンチプレス", 60, 10)])
    post = player.post("/api/posts", json={"workout_id": workout_id, "body": "胸の日", "photos": [JPEG, JPEG]}).json()
    assert len(post["photos"]) == 2
    timeline = coach.get("/api/timeline").json()["posts"]
    assert timeline[0]["body"] == "胸の日"
    assert timeline[0]["workout"]["exercises"][0]["best"] == "60kg×10回"
    photo = coach.get(post["photos"][0]["url"])
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"


def test_post_rules():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    workout_id = do_workout(player, [("ベンチプレス", 60, 10)])
    assert coach.post("/api/posts", json={"workout_id": workout_id}).status_code == 404  # 人の筋トレは 投稿できない
    assert player.post("/api/posts", json={"workout_id": workout_id, "photos": [JPEG] * 5}).status_code == 400
    assert player.post("/api/posts", json={"workout_id": workout_id, "photos": ["data:image/png;base64,AAAA"]}).status_code == 400
    assert player.post("/api/posts", json={"workout_id": workout_id}).status_code == 200
    assert player.post("/api/posts", json={"workout_id": workout_id}).status_code == 409  # 1つの筋トレに 1つ


def test_other_team_cannot_see_anything():
    red = create_team()
    player = join(red, "たなか", "tanaka")
    blue = create_team("ブルーチーム", "ブルーコーチ", "bluecoach")
    workout_id = do_workout(player, [("ベンチプレス", 60, 10)])
    post = player.post("/api/posts", json={"workout_id": workout_id, "body": "ひみつ", "photos": [JPEG]}).json()
    comment = red.post(f"/api/posts/{post['id']}/comments", json={"body": "いいね"}).json()["comments"][0]

    assert blue.get("/api/timeline").json()["posts"] == []
    assert blue.get(f"/api/posts/{post['id']}").status_code == 404
    assert blue.get(post["photos"][0]["url"]).status_code == 404
    assert blue.post(f"/api/posts/{post['id']}/comments", json={"body": "のぞき"}).status_code == 404
    assert blue.put(f"/api/posts/{post['id']}/reaction", json={"emoji": "👍"}).status_code == 404
    assert blue.put(f"/api/comments/{comment['id']}/reaction", json={"emoji": "👍"}).status_code == 404
    assert blue.delete(f"/api/comments/{comment['id']}").status_code == 404
    assert blue.delete(f"/api/posts/{post['id']}").status_code == 404
    assert blue.post(f"/api/admin/posts/{post['id']}/review").status_code == 404
    assert blue.get(f"/api/members/{me(player)['id']}").status_code == 404
    assert blue.patch(f"/api/admin/members/{me(player)['id']}", json={"active": False}).status_code == 404
    assert blue.get("/api/admin/inbox").json()["total"] == 0
    assert [m["name"] for m in blue.get("/api/members").json()] == ["ブルーコーチ"]
    assert make_client().get(post["photos"][0]["url"]).status_code == 401  # ログインしていない人にも 見えない


def test_reactions_switch_and_remove():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    post = player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)])}).json()
    coach.put(f"/api/posts/{post['id']}/reaction", json={"emoji": "👍"})
    player.put(f"/api/posts/{post['id']}/reaction", json={"emoji": "👍"})
    seen = coach.get(f"/api/posts/{post['id']}").json()["reactions"]
    assert seen == [{"emoji": "👍", "count": 2, "mine": True, "names": ["コーチ", "たなか"]}]
    # 別のを選ぶと 入れかわる（1人 1つ）
    seen = coach.put(f"/api/posts/{post['id']}/reaction", json={"emoji": "🔥"}).json()["reactions"]
    assert [(r["emoji"], r["count"]) for r in seen] == [("👍", 1), ("🔥", 1)]
    seen = coach.delete(f"/api/posts/{post['id']}/reaction").json()["reactions"]
    assert [(r["emoji"], r["count"]) for r in seen] == [("👍", 1)]
    assert coach.put(f"/api/posts/{post['id']}/reaction", json={"emoji": "🍣"}).status_code == 400


def test_comments_reactions_and_trainer_first():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    friend = join(coach, "すずき", "suzuki")
    post = player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)])}).json()
    friend.post(f"/api/posts/{post['id']}/comments", json={"body": "ナイス"})
    data = coach.post(f"/api/posts/{post['id']}/comments", json={"body": "肘を開きすぎない"}).json()
    assert [c["body"] for c in data["comments"]] == ["肘を開きすぎない", "ナイス"]  # トレーナーが上
    trainer_comment = data["comments"][0]
    seen = player.put(f"/api/comments/{trainer_comment['id']}/reaction", json={"emoji": "💪"}).json()
    assert seen["comments"][0]["reactions"][0]["emoji"] == "💪"
    # コメントを消せるのは 書いた本人とトレーナー
    friend_comment = data["comments"][1]
    assert player.delete(f"/api/comments/{friend_comment['id']}").status_code == 403
    assert coach.delete(f"/api/comments/{friend_comment['id']}").status_code == 200


def test_notices_for_post_owner():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    post = player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)])}).json()
    coach.post(f"/api/posts/{post['id']}/comments", json={"body": "いいね"})
    coach.put(f"/api/posts/{post['id']}/reaction", json={"emoji": "👍"})
    coach.put(f"/api/posts/{post['id']}/reaction", json={"emoji": "🔥"})  # スタンプの知らせは まとめて 1つ
    player.post(f"/api/posts/{post['id']}/comments", json={"body": "ありがとうございます"})  # 自分には 知らせない
    assert player.get("/api/notices/count").json()["unread"] == 2
    kinds = [n["kind"] for n in player.get("/api/notices").json()]
    assert kinds == ["reaction", "trainer_comment"]
    player.post("/api/notices/read")
    assert player.get("/api/me").json()["unread"] == 0


def test_delete_post_keeps_workout():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    workout_id = do_workout(player, [("懸垂", None, 10)])
    post = player.post("/api/posts", json={"workout_id": workout_id, "photos": [JPEG]}).json()
    coach.post(f"/api/posts/{post['id']}/comments", json={"body": "いいね"})
    coach.put(f"/api/posts/{post['id']}/reaction", json={"emoji": "👍"})
    assert player.delete(f"/api/posts/{post['id']}").status_code == 200
    assert coach.get("/api/timeline").json()["posts"] == []
    assert coach.get(post["photos"][0]["url"]).status_code == 404
    history = coach.get(f"/api/members/{me(player)['id']}").json()["history"]
    assert history[0]["id"] == workout_id and history[0]["post_id"] is None


def test_timeline_pages_and_filter():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    for _ in range(22):
        player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)])})
    coach.post("/api/posts", json={"workout_id": do_workout(coach, [("懸垂", None, 5)])})
    first = coach.get("/api/timeline").json()
    assert len(first["posts"]) == 20 and first["more"] is True
    second = coach.get(f"/api/timeline?before={first['posts'][-1]['id']}").json()
    assert len(second["posts"]) == 3 and second["more"] is False
    mine = coach.get(f"/api/timeline?user_id={me(coach)['id']}").json()["posts"]
    assert len(mine) == 1


# ---- トレーナーの管理画面 ----

def test_inbox_shows_unreviewed_with_trend_and_last_comment():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    first = player.post("/api/posts", json={"workout_id": do_workout(player, [("ベンチプレス", 80, 6), ("懸垂", None, 10)])}).json()
    coach.post(f"/api/posts/{first['id']}/comments", json={"body": "肘を開きすぎない"})  # コメントすると「見た」になる
    assert coach.get("/api/admin/inbox").json()["total"] == 0

    second = player.post("/api/posts", json={"workout_id": do_workout(player, [("ベンチプレス", 82.5, 6), ("懸垂", None, 10), ("スクワット", 100, 5)])}).json()
    inbox = coach.get("/api/admin/inbox").json()
    assert inbox["total"] == 1
    item = inbox["posts"][0]
    assert item["id"] == second["id"]
    assert item["last_trainer_comment"] == "肘を開きすぎない"
    trends = {e["name"]: e["trend"] for e in item["workout"]["exercises"]}
    assert trends["ベンチプレス"] == {"dir": "up", "previous": "80kg×6回"}
    assert trends["懸垂"] == {"dir": "same", "previous": "自重×10回"}
    assert trends["スクワット"] is None  # 初めての種目

    assert coach.post(f"/api/admin/posts/{second['id']}/review").status_code == 200  # 見ただけ
    assert coach.get("/api/admin/inbox").json()["total"] == 0


def test_quick_replies_are_per_trainer():
    coach = create_team()
    assert coach.post("/api/admin/quick-replies", json={"body": "ナイス！"}).status_code == 200
    replies = coach.get("/api/admin/quick-replies").json()
    assert [r["body"] for r in replies] == ["ナイス！"]
    assert coach.delete(f"/api/admin/quick-replies/{replies[0]['id']}").status_code == 200
    assert coach.get("/api/admin/quick-replies").json() == []


def test_regenerate_invite_code_disables_old_one():
    coach = create_team()
    old = coach.get("/api/admin/team").json()["invite_code"]
    new = coach.post("/api/admin/invite").json()["invite_code"]
    assert new != old
    body = {"name": "a", "login_id": "someone", "password": "password123"}
    assert make_client().post("/api/register", json={**body, "invite_code": old}).status_code == 404
    assert make_client().post("/api/register", json={**body, "invite_code": new}).status_code == 200


def test_member_page_has_history_and_growth_and_idle_mark():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    first = do_workout(player, [("ベンチプレス", 60, 10)])
    with Session(db.engine) as session:  # 1回目を 10日前のことにする
        workout = session.get(Workout, first)
        from datetime import timedelta
        workout.day -= timedelta(days=10)
        workout.started_at -= timedelta(days=10)
        session.commit()
    row = next(m for m in coach.get("/api/members").json() if m["name"] == "たなか")
    assert row["idle"] is True and row["days_since"] == 10 and row["week_count"] == 0

    do_workout(player, [("ベンチプレス", 62.5, 10)])
    row = next(m for m in coach.get("/api/members").json() if m["name"] == "たなか")
    assert row["idle"] is False and row["week_count"] == 1 and row["recent_pr"] is True
    detail = coach.get(f"/api/members/{me(player)['id']}").json()
    assert len(detail["history"]) == 2
    bench = detail["series"][0]
    assert bench["name"] == "ベンチプレス"
    assert [p["value"] for p in bench["points"]] == [80.0, 83.3]  # 見つもり1RM：古い順


# ---- 休憩おわりの知らせ ----

def test_rest_push_is_scheduled_and_cancelled():
    coach = create_team()
    uid = me(coach)["id"]
    assert coach.post("/api/rest/push", json={"seconds": 120, "name": "ベンチプレス"}).status_code == 200
    assert push.rest_scheduled(uid)
    assert coach.delete("/api/rest/push").status_code == 200
    assert not push.rest_scheduled(uid)


def test_rest_push_fires_at_end(monkeypatch):
    import threading

    sent = threading.Event()
    seen = {}

    def fake_deliver(user_id, payload, urgent=False):
        seen.update(user_id=user_id, payload=payload, urgent=urgent)
        sent.set()

    monkeypatch.setattr(push, "_deliver", fake_deliver)
    coach = create_team()
    coach.post("/api/rest/push", json={"seconds": 0.05, "name": "スクワット"})
    assert sent.wait(2)
    assert seen["user_id"] == me(coach)["id"]
    assert seen["payload"]["title"] == "休憩おわり！" and "スクワット" in seen["payload"]["body"]
    assert seen["urgent"] is True  # 休憩おわりは 急ぎで送る


def test_push_subscription_is_saved_once():
    coach = create_team()
    body = {"endpoint": "https://web.push.apple.com/abc", "keys": {"p256dh": "k", "auth": "a"}}
    assert coach.post("/api/push/subscribe", json=body).status_code == 200
    assert coach.post("/api/push/subscribe", json=body).status_code == 200
    assert coach.post("/api/push/subscribe", json={**body, "endpoint": "http://bad"}).status_code == 400
    assert coach.get("/api/me").json()["vapid_key"]


# ---- 体重・体脂肪率 ----

def test_start_workout_with_or_without_body_weight():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    # 入れなくても 始められる
    workout = coach.post("/api/workouts/start").json()["workout"]
    assert workout["status"] == "active" and coach.get("/api/body").json()["logs"] == []
    # 入れると 今日の体重として記録される
    assert player.post("/api/workouts/start", json={"body_weight": 70.25}).status_code == 200
    logs = player.get("/api/body").json()["logs"]
    assert [(log["weight"], log["body_fat"]) for log in logs] == [(70.2, None)]
    # おかしな数字では 始まらない
    other = join(coach, "すずき", "suzuki")
    assert other.post("/api/workouts/start", json={"body_weight": 700}).status_code == 400
    assert other.get("/api/workouts/active").json()["workout"] is None


def test_body_log_is_one_per_day_and_keeps_other_field():
    coach = create_team()
    coach.put("/api/body", json={"weight": 70})
    logs = coach.put("/api/body", json={"body_fat": 15.5}).json()["logs"]
    assert len(logs) == 1 and (logs[0]["weight"], logs[0]["body_fat"]) == (70.0, 15.5)
    logs = coach.put("/api/body", json={"weight": 69.5}).json()["logs"]  # 入れ直すと 上書き
    assert len(logs) == 1 and (logs[0]["weight"], logs[0]["body_fat"]) == (69.5, 15.5)
    assert coach.put("/api/body", json={}).status_code == 400
    assert coach.put("/api/body", json={"body_fat": 90}).status_code == 400
    assert coach.delete(f"/api/body/{logs[0]['id']}").json()["logs"] == []


def test_body_is_visible_only_to_self_and_trainer():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    friend = join(coach, "すずき", "suzuki")
    log = player.put("/api/body", json={"weight": 70, "body_fat": 15}).json()["logs"][0]
    player_id = me(player)["id"]
    assert player.get(f"/api/members/{player_id}").json()["body"][0]["weight"] == 70.0
    assert coach.get(f"/api/members/{player_id}").json()["body"][0]["weight"] == 70.0  # トレーナーは 見える
    assert friend.get(f"/api/members/{player_id}").json()["body"] is None  # ほかの選手には 体重は見えない
    assert friend.delete(f"/api/body/{log['id']}").status_code == 404
    assert coach.delete(f"/api/body/{log['id']}").status_code == 404  # 消せるのは 本人だけ


# ---- メンバーの一覧は トレーナーだけ。1人ずつのページは 名前を押せば だれでも見られる ----

def test_member_list_is_for_trainers_but_pages_are_open_to_team():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    friend = join(coach, "すずき", "suzuki")
    do_workout(player, [("ベンチプレス", 60, 10)])
    player_id = me(player)["id"]
    assert player.get("/api/members").status_code == 403  # 一覧は トレーナーだけ
    assert len(friend.get(f"/api/members/{player_id}").json()["history"]) == 1  # ほかの選手のページも 見られる
    assert len(player.get(f"/api/members/{player_id}").json()["history"]) == 1  # 自分のページは 見られる
    assert len(coach.get(f"/api/members/{player_id}").json()["history"]) == 1
    assert len(coach.get("/api/members").json()) == 3


# ---- 写真の保存期間 ----

def test_old_photos_expire_but_post_stays():
    from datetime import timedelta

    from app import config, services, storage
    from app.models import Post, PostPhoto

    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    old = player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)]), "body": "むかし", "photos": [JPEG, JPEG]}).json()
    new = player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)]), "photos": [JPEG]}).json()
    coach.post(f"/api/posts/{old['id']}/comments", json={"body": "いいね"})
    with Session(db.engine) as session:
        post = session.get(Post, old["id"])
        post.created_at -= timedelta(days=config.PHOTO_KEEP_DAYS + 1)
        session.commit()
        keys = [p.storage_key for p in session.exec(select(PostPhoto).where(PostPhoto.post_id == old["id"])).all()]
        assert services.expire_photos(session) == 0  # 今日は もう片づけたので 動かない（1日に1回）
        assert services.expire_photos(session, force=True) == 2
        assert services.expire_photos(session, force=True) == 0
    assert all(storage.read(key) is None for key in keys)  # 写真そのものが 消えている

    seen = coach.get(f"/api/posts/{old['id']}").json()
    assert seen["photos"] == [] and seen["photos_expired"] is True
    assert seen["body"] == "むかし" and len(seen["comments"]) == 1  # 記録・ひとこと・コメントは 残る
    assert seen["workout"]["exercises"][0]["best"] == "自重×10回"
    assert coach.get(old["photos"][0]["url"]).status_code == 404
    fresh = coach.get(f"/api/posts/{new['id']}").json()
    assert len(fresh["photos"]) == 1 and fresh["photos_expired"] is False  # 新しい写真は そのまま
    assert coach.get(new["photos"][0]["url"]).status_code == 200
    assert player.delete(f"/api/posts/{old['id']}").status_code == 200  # 写真が消えた投稿も 消せる


# ---- コメントへの返信 ----

def test_reply_is_nested_and_notifies_comment_author():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    friend = join(coach, "すずき", "suzuki")
    post = player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)])}).json()
    first = friend.post(f"/api/posts/{post['id']}/comments", json={"body": "ナイス"}).json()["comments"][0]
    data = coach.post(f"/api/posts/{post['id']}/comments", json={"body": "ほんとに", "parent_id": first["id"]}).json()
    reply = data["comments"][0]["replies"][0]
    assert reply["body"] == "ほんとに" and reply["parent_id"] == first["id"]
    # 返信への返信は、同じ元のコメントに付く（段は 1つだけ）
    data = player.post(f"/api/posts/{post['id']}/comments", json={"body": "ありがとう", "parent_id": reply["id"]}).json()
    assert len(data["comments"]) == 1 and data["comment_count"] == 3
    assert [r["body"] for r in data["comments"][0]["replies"]] == ["ほんとに", "ありがとう"]
    # 返信された人（すずき）に 知らせが行く。投稿した人（たなか）には コメントの知らせ
    assert [n["kind"] for n in friend.get("/api/notices").json()] == ["reply", "reply"]
    assert [n["kind"] for n in player.get("/api/notices").json()] == ["trainer_comment", "comment"]
    # ほかの投稿のコメントには 返信できない
    other = player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)])}).json()
    assert coach.post(f"/api/posts/{other['id']}/comments", json={"body": "x", "parent_id": first["id"]}).status_code == 404


def test_deleting_comment_or_post_removes_replies():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    post = player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)])}).json()
    first = coach.post(f"/api/posts/{post['id']}/comments", json={"body": "いいね"}).json()["comments"][0]
    data = player.post(f"/api/posts/{post['id']}/comments", json={"body": "どうも", "parent_id": first["id"]}).json()
    coach.put(f"/api/comments/{data['comments'][0]['replies'][0]['id']}/reaction", json={"emoji": "👍"})
    data = coach.delete(f"/api/comments/{first['id']}").json()
    assert data["comments"] == [] and data["comment_count"] == 0
    first = coach.post(f"/api/posts/{post['id']}/comments", json={"body": "いいね"}).json()["comments"][0]
    player.post(f"/api/posts/{post['id']}/comments", json={"body": "どうも", "parent_id": first["id"]})
    assert player.delete(f"/api/posts/{post['id']}").status_code == 200


# ---- あとから記録 ----

def test_backfill_records_past_day_with_given_minutes():
    from datetime import timedelta

    from app import clock

    coach = create_team()
    day = clock.today() - timedelta(days=3)
    workout = coach.post("/api/workouts/start", json={"day": day.isoformat(), "minutes": 75, "body_weight": 70}).json()["workout"]
    assert workout["backfill"] is True and workout["day"] == day.isoformat()
    assert coach.get("/api/body").json()["logs"] == []  # 体重は「今日の分」なので、あとから記録では 入らない
    coach.post(f"/api/workouts/{workout['id']}/sets", json={"exercise_id": exercise_id(coach, "ベンチプレス"), "weight": 60, "reps": 10})
    done = coach.post(f"/api/workouts/{workout['id']}/finish").json()["workout"]
    assert done["minutes"] == 75 and done["day"] == day.isoformat()
    post = coach.post("/api/posts", json={"workout_id": workout["id"]}).json()
    assert post["workout"]["backfill"] is True and post["workout"]["day"] == day.isoformat()
    history = coach.get(f"/api/members/{me(coach)['id']}").json()["history"]
    assert history[0]["day"] == day.isoformat() and history[0]["minutes"] == 75


def test_backfill_limits():
    from datetime import timedelta

    from app import clock

    coach = create_team()
    too_old = (clock.today() - timedelta(days=8)).isoformat()
    future = (clock.today() + timedelta(days=1)).isoformat()
    assert coach.post("/api/workouts/start", json={"day": too_old, "minutes": 60}).status_code == 400
    assert coach.post("/api/workouts/start", json={"day": future, "minutes": 60}).status_code == 400
    assert coach.post("/api/workouts/start", json={"day": clock.today().isoformat(), "minutes": 999}).status_code == 400
    assert coach.get("/api/workouts/active").json()["workout"] is None
    normal = coach.post("/api/workouts/start").json()["workout"]
    assert normal["backfill"] is False


# ---- トレーナーからのメニュー ----

def test_trainer_menu_flow():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    bench, squat = exercise_id(coach, "ベンチプレス"), exercise_id(coach, "スクワット")
    body = {"title": "今週のメニュー", "note": "フォーム重視で", "items": [
        {"exercise_id": bench, "sets": 3, "reps": 8, "weight": 60},
        {"exercise_id": squat, "sets": 5, "reps": 5},
    ]}
    assert player.post("/api/admin/menus", json=body).status_code == 403  # 作れるのは トレーナーだけ
    menu_id = coach.post("/api/admin/menus", json=body).json()["id"]
    menus = player.get("/api/menus").json()
    assert [m["title"] for m in menus] == ["今週のメニュー"]
    assert [(i["name"], i["label"]) for i in menus[0]["items"]] == [("ベンチプレス", "60kg×8回×3セット"), ("スクワット", "5回×5セット")]

    workout = player.post("/api/workouts/start", json={"menu_id": menu_id}).json()["workout"]
    assert workout["menu"]["title"] == "今週のメニュー" and len(workout["menu"]["items"]) == 2
    player.post(f"/api/workouts/{workout['id']}/sets", json={"exercise_id": bench, "weight": 60, "reps": 8})
    player.post(f"/api/workouts/{workout['id']}/finish")
    post = player.post("/api/posts", json={"workout_id": workout["id"]}).json()
    assert post["workout"]["menu_title"] == "今週のメニュー"

    # 配るのをやめると 一覧から消える。そのメニューでやった記録は 残る
    assert coach.delete(f"/api/admin/menus/{menu_id}").status_code == 200
    assert player.get("/api/menus").json() == []
    assert coach.get(f"/api/posts/{post['id']}").json()["workout"]["menu_title"] == "今週のメニュー"
    assert coach.post("/api/workouts/start", json={"menu_id": menu_id}).status_code == 404


def test_menu_rules_and_team_wall():
    red = create_team()
    blue = create_team("ブルーチーム", "ブルーコーチ", "bluecoach")
    bench, run = exercise_id(red, "ベンチプレス"), exercise_id(red, "ランニング")
    item = {"exercise_id": bench, "sets": 3, "reps": 8}
    assert red.post("/api/admin/menus", json={"title": "", "items": [item]}).status_code == 400
    assert red.post("/api/admin/menus", json={"title": "a", "items": []}).status_code == 400
    assert red.post("/api/admin/menus", json={"title": "a", "items": [{**item, "sets": 0}]}).status_code == 400
    assert red.post("/api/admin/menus", json={"title": "a", "items": [{**item, "exercise_id": run}]}).status_code == 400
    menu_id = red.post("/api/admin/menus", json={"title": "レッド用", "items": [item]}).json()["id"]
    assert blue.get("/api/menus").json() == []
    assert blue.post("/api/workouts/start", json={"menu_id": menu_id}).status_code == 404
    assert blue.delete(f"/api/admin/menus/{menu_id}").status_code == 404
    assert len(red.get("/api/menus").json()) == 1


# ---- データベースの引っ越し ----

def test_missing_columns_are_added_to_old_tables():
    from sqlalchemy import inspect, text

    coach = create_team()
    post = coach.post("/api/posts", json={"workout_id": do_workout(coach, [("懸垂", None, 10)])}).json()
    with db.engine.begin() as conn:  # 前の版の形にもどす（列を消す）
        conn.execute(text('ALTER TABLE "workout" DROP COLUMN planned_minutes'))  # 空でもいい列
        conn.execute(text('ALTER TABLE "team" DROP COLUMN max_trainers'))  # 空ではだめな列（最初の値が入る）
    assert sorted(db.add_missing_columns()) == ["team.max_trainers", "workout.planned_minutes"]
    assert db.add_missing_columns() == []  # 2回目は 何もしない
    with db.engine.begin() as conn:
        assert "planned_minutes" in {c["name"] for c in inspect(conn).get_columns("workout")}
    # 前からあるデータは そのまま読める。足した列には 最初の値が入っている
    assert coach.get(f"/api/posts/{post['id']}").json()["workout"]["backfill"] is False
    assert coach.get("/api/admin/team").json()["trainers"] == {"count": 1, "max": 10}


def test_menu_can_be_shared_as_photos_only():
    from app import storage
    from app.models import MenuPhoto

    red = create_team()
    player = join(red, "たなか", "tanaka")
    blue = create_team("ブルーチーム", "ブルーコーチ", "bluecoach")
    assert red.post("/api/admin/menus", json={"title": "写真メニュー", "photos": [JPEG] * 5}).status_code == 400
    assert red.post("/api/admin/menus", json={"title": "写真メニュー", "photos": ["data:image/png;base64,AAAA"]}).status_code == 400
    menu_id = red.post("/api/admin/menus", json={"title": "今週のメニュー（写真）", "photos": [JPEG, JPEG]}).json()["id"]
    menu = player.get("/api/menus").json()[0]
    assert menu["items"] == [] and len(menu["photos"]) == 2
    url = menu["photos"][0]["url"]
    assert player.get(url).status_code == 200 and player.get(url).headers["content-type"] == "image/jpeg"
    assert blue.get(url).status_code == 404  # ほかのチームには 見えない
    assert make_client().get(url).status_code == 401

    # 写真だけのメニューでも、それで始めて 記録できる
    workout = player.post("/api/workouts/start", json={"menu_id": menu_id}).json()["workout"]
    assert len(workout["menu"]["photos"]) == 2 and workout["menu"]["items"] == []
    player.post(f"/api/workouts/{workout['id']}/sets", json={"exercise_id": exercise_id(player, "ベンチプレス"), "weight": 60, "reps": 8})
    player.post(f"/api/workouts/{workout['id']}/finish")
    post = player.post("/api/posts", json={"workout_id": workout["id"]}).json()
    assert post["workout"]["menu_title"] == "今週のメニュー（写真）"

    # 配るのをやめると、写真も消える（記録に付いた メニューの名前は 残る）
    with Session(db.engine) as session:
        keys = [p.storage_key for p in session.exec(select(MenuPhoto).where(MenuPhoto.menu_id == menu_id)).all()]
    assert red.delete(f"/api/admin/menus/{menu_id}").status_code == 200
    assert player.get(url).status_code == 404 and all(storage.read(key) is None for key in keys)
    assert red.get(f"/api/posts/{post['id']}").json()["workout"]["menu_title"] == "今週のメニュー（写真）"


def test_menu_period_and_delivery(monkeypatch):
    from datetime import timedelta

    from app import clock

    pushed = []
    monkeypatch.setattr(push, "send", lambda user_id, payload: pushed.append((user_id, payload["title"])))
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    gone = join(coach, "やめた", "yameta")
    coach.patch(f"/api/admin/members/{me(gone)['id']}", json={"active": False})
    today = clock.today()
    item = {"exercise_id": exercise_id(coach, "ベンチプレス"), "sets": 3, "reps": 8}

    def make(title, start, end):
        body = {"title": title, "note": "コメント", "items": [item],
                "start_day": (today + timedelta(days=start)).isoformat(), "end_day": (today + timedelta(days=end)).isoformat()}
        return coach.post("/api/admin/menus", json=body)

    assert make("逆", 3, 1).status_code == 400  # 終わりが 始まりより前
    assert make("先月", -250, -200).status_code == 200
    assert make("先週", -10, -4).status_code == 200
    assert make("来週", 5, 11).status_code == 200
    assert make("今週", -2, 4).status_code == 200
    # 配ると、チームのみんな（自分と やめた人以外）に 通知が行く
    assert pushed == [(me(player)["id"], f"新しいメニュー：{title}") for title in ("先月", "先週", "来週", "今週")]

    # 期間がすぎても 半年は「過去のメニュー」として返す（先週）。それより前に終わったもの（先月）は 出ない
    seen = player.get("/api/menus").json()
    assert [(m["title"], m["status"]) for m in seen] == [("今週", "now"), ("来週", "soon"), ("先週", "over")]
    assert seen[0]["start_day"] == (today - timedelta(days=2)).isoformat() and seen[0]["note"] == "コメント"
    everything = coach.get("/api/admin/menus").json()  # トレーナーには 終わったものも見える
    assert {m["title"]: m["status"] for m in everything} == {"今週": "now", "来週": "soon", "先週": "over", "先月": "over"}
    assert player.get("/api/admin/menus").status_code == 403
    # 期間を決めないメニューは ずっと出る
    assert coach.post("/api/admin/menus", json={"title": "いつでも", "items": [item]}).status_code == 200
    assert "いつでも" in [m["title"] for m in player.get("/api/menus").json()]


def test_push_accepts_only_real_push_servers():
    coach = create_team()
    keys = {"p256dh": "k", "auth": "a"}
    for endpoint in ("https://evil.example/hook", "http://web.push.apple.com/abc", "https://push.apple.com.evil.example/x",
                     "https://fcm.googleapis.com.evil.example/x"):
        assert coach.post("/api/push/subscribe", json={"endpoint": endpoint, "keys": keys}).status_code == 400, endpoint
    for endpoint in ("https://web.push.apple.com/a", "https://fcm.googleapis.com/fcm/send/a",
                     "https://updates.push.services.mozilla.com/wpush/v2/a", "https://wns2-par02p.notify.windows.com/w/?token=a"):
        assert push.allowed_endpoint(endpoint), endpoint


def test_push_keeps_at_most_five_per_user_and_forgets_gone_ones():
    from app.models import PushSub

    coach = create_team()
    uid = me(coach)["id"]
    for i in range(7):
        coach.post("/api/push/subscribe", json={"endpoint": f"https://web.push.apple.com/{i}", "keys": {"p256dh": "k", "auth": "a"}})
    with Session(db.engine) as session:
        endpoints = [s.endpoint for s in session.exec(select(PushSub).where(PushSub.user_id == uid).order_by(PushSub.id)).all()]
    assert endpoints == [f"https://web.push.apple.com/{i}" for i in range(2, 7)]  # 古いものから忘れる

    class Gone(Exception):
        response = type("R", (), {"status_code": 410})()

    calls = []

    def fake_send(sub, payload, urgent):
        calls.append((sub.endpoint, urgent))
        if sub.endpoint.endswith("/2"):
            raise Gone()  # スマホ側で 通知をやめた
        if sub.endpoint.endswith("/3"):
            raise TimeoutError()  # 一時的に 送れなかった（忘れない）

    assert push._deliver(uid, {"title": "t"}, urgent=True, send=fake_send) == 3
    assert all(urgent for _, urgent in calls)
    with Session(db.engine) as session:
        left = [s.endpoint for s in session.exec(select(PushSub).where(PushSub.user_id == uid)).all()]
    assert "https://web.push.apple.com/2" not in left and "https://web.push.apple.com/3" in left and len(left) == 4


def test_push_subject_prefers_setting_then_render_url(monkeypatch):
    monkeypatch.delenv("VAPID_SUBJECT", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://team-kintore.onrender.com")
    assert push.subject() == "https://team-kintore.onrender.com"
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:team@example.org")
    assert push.subject() == "mailto:team@example.org"


def test_generated_keys_work_with_the_push_library():
    import importlib.util
    from pathlib import Path

    from py_vapid import Vapid

    spec = importlib.util.spec_from_file_location("make_vapid_keys", Path(__file__).resolve().parent.parent / "scripts" / "make_vapid_keys.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    public, private = module.make_keys()
    assert len(public) == 87 and len(private) == 43
    headers = Vapid.from_string(private).sign({"sub": "https://team-kintore.onrender.com", "aud": "https://web.push.apple.com"})
    assert headers["Authorization"].startswith("vapid ")
