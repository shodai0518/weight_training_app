"""第6版で足したもの：競技・記録の仕方・種目ごとのメモ・セットの直し・投稿の写真の入れかえ・
スタンプの知らせのまとめ・種目の直しとまとめ・メニューの有酸素と 人ごとの配信。"""

import base64

from sqlmodel import Session, select

from app import db
from app.models import Exercise, MenuItem, UserExercise, WorkoutSet
from tests.conftest import create_team, do_workout, exercise_id, join, make_client

JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 200).decode()


def me(client):
    return client.get("/api/me").json()["user"]


def exercises(client):
    return {e["name"]: e for e in client.get("/api/exercises").json()["exercises"]}


def start(client, **body):
    return client.post("/api/workouts/start", json=body).json()["workout"]


def make_exercise(client, name, part, kind):
    assert client.post("/api/exercises", json={"name": name, "part": part, "kind": kind}).status_code == 200


def add(client, workout_id, name, **values):
    return client.post(f"/api/workouts/{workout_id}/sets", json={"exercise_id": exercise_id(client, name), **values})


# ---- 競技 ----

def test_team_sport_adds_sport_tab_and_exercises():
    football = create_team()
    baseball = create_team("ベースボール", "野球コーチ", "baseballcoach", sport="baseball")
    assert football.get("/api/me").json()["parts"]["sport"] == "アメフト"
    assert baseball.get("/api/me").json()["parts"]["sport"] == "野球"
    mine = exercises(football)
    assert mine["カット練"]["part"] == "sport" and mine["カット練"]["kind"] == "count"
    assert mine["ヒット練"]["kind"] == "count" and mine["7on7"]["kind"] == "minutes"
    assert mine["7on7"]["rest_seconds"] is None and mine["カット練"]["rest_seconds"] == 90  # 時間（分）の練習は 休憩タイマーなし
    assert "パワークリーン" not in mine  # 前の版の 筋トレの種目は もう 競技の欄にない
    assert "カット練" not in exercises(baseball)  # ほかの競技のチームには 出ない
    assert exercises(baseball)["プランク"]["kind"] == "time"  # 共通の種目は どのチームにも出る
    assert make_client().post("/api/teams", json={"team_name": "x", "name": "x", "login_id": "xxx", "password": "password123"}).status_code == 400
    assert "football" in make_client().get("/api/setup").json()["sports"]


def test_trainer_can_change_sport_and_add_sport_exercise():
    coach = create_team(sport="other")
    player = join(coach, "たなか", "tanaka")
    assert coach.post("/api/exercises", json={"name": "タックルドリル", "part": "sport", "kind": "reps"}).status_code == 200
    assert player.patch("/api/admin/team", json={"sport": "football"}).status_code == 403
    assert coach.patch("/api/admin/team", json={"sport": "football"}).status_code == 200
    names = exercises(player)
    assert "カット練" in names and names["タックルドリル"]["part"] == "sport"  # チームで足した種目は 残る
    assert coach.post("/api/exercises", json={"name": "x", "part": "chest", "kind": "nope"}).status_code == 400


# ---- 記録の仕方 ----

def test_record_kinds_labels_and_best():
    coach = create_team()
    make_exercise(coach, "ダッシュ", "legs", "distance_sets")
    make_exercise(coach, "ボックスジャンプ", "legs", "reps")
    workout = start(coach)
    assert add(coach, workout["id"], "カット練", reps=10).status_code == 200
    assert add(coach, workout["id"], "7on7", duration_min=20).status_code == 200
    assert add(coach, workout["id"], "7on7", reps=3).status_code == 400  # 分が いる
    assert add(coach, workout["id"], "プランク", seconds=60).status_code == 200
    assert add(coach, workout["id"], "プランク", seconds=90).status_code == 200
    assert add(coach, workout["id"], "ダッシュ", distance_m=40, reps=10).status_code == 200
    assert add(coach, workout["id"], "ボックスジャンプ", reps=12).status_code == 200
    assert add(coach, workout["id"], "ランニング", duration_min=20, distance_km=4).status_code == 200
    assert add(coach, workout["id"], "プランク", reps=10).status_code == 400  # 秒が いる
    assert add(coach, workout["id"], "ダッシュ", distance_m=40).status_code == 400  # 本数が いる
    done = coach.post(f"/api/workouts/{workout['id']}/finish").json()["workout"]
    summary = {e["name"]: e for e in done["exercises"]}
    assert summary["プランク"]["best"] == "90秒" and summary["プランク"]["kind"] == "time"
    assert summary["ダッシュ"]["best"] == "40m×10本"
    assert summary["ボックスジャンプ"]["best"] == "12回"
    assert summary["ランニング"]["best"] == "20分・4km"
    assert summary["カット練"]["best"] == "10本" and summary["7on7"]["best"] == "20分"


def test_personal_best_for_time_and_graph_units():
    coach = create_team()
    do_workout_kind(coach, "プランク", seconds=60)
    workout = start(coach)
    assert add(coach, workout["id"], "プランク", seconds=75).json()["is_pr"] is True
    coach.post(f"/api/workouts/{workout['id']}/finish")
    series = {s["name"]: s for s in coach.get(f"/api/members/{me(coach)['id']}").json()["series"]}
    assert [(p["value"], p["unit"]) for p in series["プランク"]["points"]] == [(60.0, "秒"), (75.0, "秒")]


def do_workout_kind(client, name, **values):
    workout = start(client)
    assert add(client, workout["id"], name, **values).status_code == 200
    client.post(f"/api/workouts/{workout['id']}/finish")
    return workout["id"]


# ---- 種目ごとのメモ ----

def test_exercise_notes_show_in_post_and_history():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    workout = start(player)
    add(player, workout["id"], "ベンチプレス", weight=60, reps=10)
    add(player, workout["id"], "懸垂", reps=8)
    bench = exercise_id(player, "ベンチプレス")
    data = player.put(f"/api/workouts/{workout['id']}/notes/{bench}", json={"body": "右肩が少し痛い"}).json()["workout"]
    assert {e["name"]: e["note"] for e in data["exercises"]} == {"ベンチプレス": "右肩が少し痛い", "懸垂": ""}
    squat = exercise_id(player, "スクワット")
    assert player.put(f"/api/workouts/{workout['id']}/notes/{squat}", json={"body": "x"}).status_code == 404  # やっていない種目
    assert coach.put(f"/api/workouts/{workout['id']}/notes/{bench}", json={"body": "x"}).status_code == 404  # 人の筋トレ
    assert player.put(f"/api/workouts/{workout['id']}/notes/{bench}", json={"body": "あ" * 201}).status_code == 400
    player.post(f"/api/workouts/{workout['id']}/finish")
    post = player.post("/api/posts", json={"workout_id": workout["id"], "body": "全体のひとこと"}).json()
    seen = coach.get(f"/api/posts/{post['id']}").json()
    assert seen["body"] == "全体のひとこと" and seen["workout"]["exercises"][0]["note"] == "右肩が少し痛い"
    # 投稿したあとでも 直せる。空にすると 消える
    player.put(f"/api/workouts/{workout['id']}/notes/{bench}", json={"body": ""})
    assert coach.get(f"/api/posts/{post['id']}").json()["workout"]["exercises"][0]["note"] == ""
    history = coach.get(f"/api/members/{me(player)['id']}").json()["history"]
    assert history[0]["exercises"][0]["note"] == ""


# ---- 投稿する前に セットを直す ----

def test_sets_can_be_fixed_until_posted():
    coach = create_team()
    do_workout(coach, [("ベンチプレス", 60, 10)])
    workout = start(coach)
    add(coach, workout["id"], "ベンチプレス", weight=80, reps=5)
    add(coach, workout["id"], "懸垂", reps=10)
    coach.post(f"/api/workouts/{workout['id']}/finish")
    sets = {e["name"]: e["sets"] for e in coach.get(f"/api/workouts/{workout['id']}").json()["workout"]["exercises"]}
    bench_set = sets["ベンチプレス"][0]
    assert bench_set["is_pr"] is True
    # 直すと 自己ベストの印も 付け直す（60kg×10回 より 弱くなった）
    data = coach.patch(f"/api/workouts/sets/{bench_set['id']}", json={"weight": 50, "reps": 5}).json()["workout"]
    fixed = data["exercises"][0]["sets"][0]
    assert fixed["label"] == "50kg×5回" and fixed["is_pr"] is False
    assert coach.patch(f"/api/workouts/sets/{bench_set['id']}", json={"weight": 50}).status_code == 400  # 回数が いる
    # 消すと、その種目のメモも消える
    pullup = sets["懸垂"][0]
    coach.put(f"/api/workouts/{workout['id']}/notes/{exercise_id(coach, '懸垂')}", json={"body": "メモ"})
    data = coach.delete(f"/api/workouts/sets/{pullup['id']}").json()["workout"]
    assert [e["name"] for e in data["exercises"]] == ["ベンチプレス"]
    # 投稿したら もう直せない
    coach.post("/api/posts", json={"workout_id": workout["id"]})
    assert coach.patch(f"/api/workouts/sets/{bench_set['id']}", json={"weight": 70, "reps": 5}).status_code == 409
    assert coach.delete(f"/api/workouts/sets/{bench_set['id']}").status_code == 409
    other = join(coach, "たなか", "tanaka")
    assert other.patch(f"/api/workouts/sets/{bench_set['id']}", json={"weight": 1, "reps": 1}).status_code == 404


# ---- 投稿したあとで 写真を入れかえる ----

def test_post_photos_can_be_added_and_removed_after_posting():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    post = player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)]), "photos": [JPEG, JPEG]}).json()
    data = player.post(f"/api/posts/{post['id']}/photos", json={"photos": [JPEG, JPEG]}).json()
    assert len(data["photos"]) == 4
    assert player.post(f"/api/posts/{post['id']}/photos", json={"photos": [JPEG]}).status_code == 400  # 4枚まで
    removed = data["photos"][0]
    data = player.delete(f"/api/posts/{post['id']}/photos/{removed['id']}").json()
    assert len(data["photos"]) == 3 and coach.get(removed["url"]).status_code == 404
    assert coach.post(f"/api/posts/{post['id']}/photos", json={"photos": [JPEG]}).status_code == 403  # 人の投稿
    assert coach.delete(f"/api/posts/{post['id']}/photos/{data['photos'][0]['id']}").status_code == 403
    assert player.patch(f"/api/posts/{post['id']}", json={"body": "書き直した"}).json()["body"] == "書き直した"


# ---- スタンプの知らせを まとめる ----

def test_reaction_notices_are_grouped_per_post():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    others = [join(coach, name, login) for name, login in (("すずき", "suzuki"), ("さとう", "sato"), ("いとう", "ito"))]
    post = player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)])}).json()
    coach.put(f"/api/posts/{post['id']}/reaction", json={"emoji": "👍"})
    player.post("/api/notices/read")
    for client, emoji in zip(others, ("🔥", "👍", "💪")):
        client.put(f"/api/posts/{post['id']}/reaction", json={"emoji": emoji})
    player.put(f"/api/posts/{post['id']}/reaction", json={"emoji": "😂"})  # 自分のスタンプは 数えない
    notices = player.get("/api/notices").json()
    assert len(notices) == 1 and notices[0]["unread"] is True  # 1つにまとまって、まだ読んでいない にもどる
    assert notices[0]["text"] == "いとう さん、さとう さん ほか2人が スタンプを付けました 👍🔥💪"
    assert [w["name"] for w in notices[0]["who"]] == ["いとう", "さとう", "すずき", "コーチ"]
    assert player.get("/api/notices/count").json()["unread"] == 1


# ---- 種目の名前の直し・まとめ ----

def test_rename_and_merge_team_exercise():
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    assert coach.post("/api/exercises", json={"name": "ベンチ", "part": "chest"}).status_code == 200
    bench_short, bench = exercise_id(coach, "ベンチ"), exercise_id(coach, "ベンチプレス")
    preset = exercise_id(coach, "スクワット")
    assert coach.patch(f"/api/admin/exercises/{preset}", json={"name": "スクワ"}).status_code == 404  # 用意した種目は 直せない
    assert coach.patch(f"/api/admin/exercises/{bench_short}", json={"name": "ベンチプレス"}).status_code == 409
    assert coach.patch(f"/api/admin/exercises/{bench_short}", json={"name": "ベンチ（バーベル）"}).status_code == 200
    assert player.patch(f"/api/admin/exercises/{bench_short}", json={"name": "x"}).status_code == 403

    do_workout(player, [("ベンチプレス", 60, 10)])
    workout = start(player)
    add(player, workout["id"], "ベンチ（バーベル）", weight=50, reps=10)
    add(player, workout["id"], "ベンチ（バーベル）", weight=55, reps=10)
    player.put(f"/api/workouts/{workout['id']}/notes/{bench_short}", json={"body": "軽め"})
    player.put(f"/api/exercises/{bench_short}/rest", json={"seconds": 150})
    player.post(f"/api/workouts/{workout['id']}/finish")
    menu = coach.post("/api/admin/menus", json={"title": "m", "items": [{"exercise_id": bench_short, "sets": 3, "reps": 8}]}).json()
    assert coach.get("/api/admin/exercises").json()[0]["sets"] == 2

    run = exercise_id(coach, "ランニング")
    assert coach.post(f"/api/admin/exercises/{bench_short}/merge", json={"into_id": run}).status_code == 400  # 記録の仕方がちがう
    assert coach.post(f"/api/admin/exercises/{bench_short}/merge", json={"into_id": bench}).json()["moved_sets"] == 2
    assert "ベンチ（バーベル）" not in exercises(player)
    summary = player.get(f"/api/workouts/{workout['id']}").json()["workout"]["exercises"][0]
    assert summary["name"] == "ベンチプレス" and summary["note"] == "軽め"
    # 1本にまとまったので 自己ベストを付け直す：60kg×10回 が先にあるので、50・55kg は 自己ベストではない
    assert [s["is_pr"] for s in summary["sets"]] == [False, False]
    with Session(db.engine) as session:
        assert session.get(Exercise, bench_short) is None
        assert session.get(UserExercise, (me(player)["id"], bench)).rest_seconds == 150
        assert session.exec(select(MenuItem).where(MenuItem.menu_id == menu["id"])).one().exercise_id == bench
        assert len(session.exec(select(WorkoutSet).where(WorkoutSet.exercise_id == bench)).all()) == 3


# ---- メニュー：有酸素・記録の仕方ごとの目標・人ごとに配る ----

def test_menu_with_cardio_and_other_kinds():
    coach = create_team()
    make_exercise(coach, "ダッシュ", "legs", "distance_sets")
    make_exercise(coach, "ボックスジャンプ", "legs", "reps")
    ids = {name: exercise_id(coach, name) for name in ("ランニング", "プランク", "ダッシュ", "ボックスジャンプ", "ベンチプレス", "カット練", "7on7")}
    body = {"title": "コンディショニング", "items": [
        {"exercise_id": ids["ランニング"], "minutes": 30, "distance_km": 5},
        {"exercise_id": ids["プランク"], "seconds": 60, "sets": 3},
        {"exercise_id": ids["ダッシュ"], "distance_m": 40, "reps": 10, "sets": 2},
        {"exercise_id": ids["ボックスジャンプ"], "reps": 8, "sets": 3},
        {"exercise_id": ids["ベンチプレス"], "weight": 60, "reps": 8, "sets": 3},
        {"exercise_id": ids["カット練"], "reps": 10, "sets": 3},
        {"exercise_id": ids["7on7"], "minutes": 20},
    ]}
    assert coach.post("/api/admin/menus", json=body).status_code == 200
    labels = [i["label"] for i in coach.get("/api/menus").json()[0]["items"]]
    assert labels == ["30分・5km", "60秒×3セット", "40m×10本×2セット", "8回×3セット", "60kg×8回×3セット", "10本×3セット", "20分×1セット"]
    bad = {"title": "x", "items": [{"exercise_id": ids["ランニング"]}]}
    assert coach.post("/api/admin/menus", json=bad).status_code == 400  # 有酸素は 時間か 距離が いる
    bad = {"title": "x", "items": [{"exercise_id": ids["プランク"], "reps": 3}]}
    assert coach.post("/api/admin/menus", json=bad).status_code == 400  # 秒が いる


def test_menu_can_be_sent_to_chosen_members(monkeypatch):
    from app import push

    sent = []
    monkeypatch.setattr(push, "send", lambda user_id, payload: sent.append(user_id))
    coach = create_team()
    linemen = [join(coach, name, login) for name, login in (("たなか", "tanaka"), ("すずき", "suzuki"))]
    other = join(coach, "さとう", "sato")
    target_ids = [me(c)["id"] for c in linemen]
    item = {"exercise_id": exercise_id(coach, "ベンチプレス"), "sets": 3, "reps": 8}
    menu_id = coach.post("/api/admin/menus", json={"title": "ライン用", "items": [item], "targets": target_ids}).json()["id"]
    assert sorted(sent) == sorted(target_ids)  # 選んだ人にだけ 通知
    assert [m["title"] for m in linemen[0].get("/api/menus").json()] == ["ライン用"]
    assert other.get("/api/menus").json() == []
    assert other.post("/api/workouts/start", json={"menu_id": menu_id}).status_code == 404
    admin = coach.get("/api/admin/menus").json()[0]
    assert sorted(t["name"] for t in admin["targets"]) == ["すずき", "たなか"]
    assert "targets" not in linemen[0].get("/api/menus").json()[0]  # 選手には だれに配ったかは 出さない
    blue = create_team("ブルー", "ブルーコーチ", "bluecoach")
    blue_id = me(blue)["id"]
    assert coach.post("/api/admin/menus", json={"title": "x", "items": [item], "targets": [blue_id]}).status_code == 404


def test_old_sport_presets_are_removed_unless_used():
    from app.models import Exercise, Workout, WorkoutSet

    coach = create_team()
    with Session(db.engine) as session:  # 前の版で 競技の欄に入れていた 筋トレの種目
        unused = Exercise(name="パワークリーン", part="sport", kind="weight_reps", sport="football", preset=True)
        used = Exercise(name="ハングクリーン", part="sport", kind="weight_reps", sport="football", preset=True)
        session.add(unused)
        session.add(used)
        session.commit()
        unused_id, used_id = unused.id, used.id
    workout = start(coach)
    assert add(coach, workout["id"], "ハングクリーン", weight=60, reps=5).status_code == 200
    with Session(db.engine) as session:
        db.seed_presets(session)
        assert session.get(Exercise, unused_id) is None  # だれも使っていない → 消す
        moved = session.get(Exercise, used_id)  # 記録がある → そのチームの種目にして、脚の欄へ（記録は そのまま）
        assert moved.part == "legs" and moved.sport is None and moved.preset is False and moved.team_id == coach.get("/api/me").json()["team"]["id"]
    names = exercises(coach)
    assert names["ハングクリーン"]["part"] == "legs" and "パワークリーン" not in names
    assert coach.get(f"/api/workouts/{workout['id']}").json()["workout"]["exercises"][0]["name"] == "ハングクリーン"
    other = create_team("ブルー", "ブルーコーチ", "bluecoach")
    assert "ハングクリーン" not in exercises(other)  # ほかのチームには 出ない


# ---- ポジション ----

def test_positions_are_chosen_and_sorted():
    coach = create_team()
    players = {name: join(coach, name, login) for name, login in (("たなか", "tanaka"), ("すずき", "suzuki"), ("さとう", "sato"))}
    data = players["たなか"].get("/api/me").json()
    assert data["positions"][:3] == ["QB", "RB", "WR"] and data["user"]["position"] == ""
    assert players["たなか"].put("/api/me/position", json={"position": "OL"}).json()["position"] == "OL"
    assert players["すずき"].put("/api/me/position", json={"position": "QB"}).status_code == 200
    assert players["さとう"].put("/api/me/position", json={"position": "ピッチャー"}).status_code == 400
    # メンバーの一覧は ポジションの順（決めていない人は 選手の最後、トレーナーは その後）
    rows = coach.get("/api/members").json()
    assert [(m["name"], m["position"]) for m in rows] == [("すずき", "QB"), ("たなか", "OL"), ("さとう", ""), ("コーチ", "")]
    # トレーナーが 変えられる。空にすると 未設定
    sato = next(m for m in rows if m["name"] == "さとう")
    assert coach.patch(f"/api/admin/members/{sato['id']}", json={"position": "DL"}).status_code == 200
    assert coach.patch(f"/api/admin/members/{sato['id']}", json={"position": "XX"}).status_code == 400
    team = coach.get("/api/admin/team").json()
    assert team["positions"][-1] == "その他"
    assert {u["name"]: u["position"] for u in team["users"]}["さとう"] == "DL"
    # 投稿の名前にも ポジションが付く
    post = players["たなか"].post("/api/posts", json={"workout_id": do_workout(players["たなか"], [("懸垂", None, 10)])}).json()
    assert post["user"]["position"] == "OL"


def test_no_positions_for_sports_without_list():
    coach = create_team(sport="swimming")
    player = join(coach, "たなか", "tanaka")
    assert player.get("/api/me").json()["positions"] == []
    assert player.put("/api/me/position", json={"position": "QB"}).status_code == 400


def test_member_list_shows_latest_body():
    from datetime import date, timedelta

    from app.models import BodyLog

    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    join(coach, "すずき", "suzuki")
    uid = me(player)["id"]
    with Session(db.engine) as session:  # 体重は 3日前、体脂肪率は きのう に入れた
        session.add(BodyLog(user_id=uid, day=date.today() - timedelta(days=3), weight=70.0, body_fat=16.0))
        session.add(BodyLog(user_id=uid, day=date.today() - timedelta(days=1), body_fat=15.5))
        session.commit()
    rows = {m["name"]: m for m in coach.get("/api/members").json()}
    body = rows["たなか"]["body"]
    assert (body["weight"], body["body_fat"]) == (70.0, 15.5)
    assert body["weight_day"] == (date.today() - timedelta(days=3)).isoformat()
    assert body["body_fat_day"] == (date.today() - timedelta(days=1)).isoformat()
    assert rows["すずき"]["body"] is None
    assert player.get("/api/members").status_code == 403  # 一覧は トレーナーだけ
