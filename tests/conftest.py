"""テストの準備。本物のデータにさわらないように、テスト用のフォルダを使う。"""

import os
import shutil
import tempfile

_DATA = tempfile.mkdtemp(prefix="kintore_team_test_")
os.environ["APP_DATA_DIR"] = _DATA  # app を読みこむ前に決める
# TEST_DATABASE_URL があれば その PostgreSQL で試す（本番と同じ種類のデータベース）。なければ テスト用の SQLite
if os.getenv("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
else:
    os.environ.pop("DATABASE_URL", None)
os.environ.pop("OPERATOR_CODE", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app import auth, db, storage  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """テストごとに 空のデータベースから始める。"""
    SQLModel.metadata.drop_all(db.engine)
    shutil.rmtree(storage.PHOTO_DIR, ignore_errors=True)
    auth._failures.clear()
    db.init_db()
    yield


def make_client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def new_client():
    """ログインしていない 新しいブラウザを作る。"""
    return make_client


def create_team(team_name="レッドチーム", name="コーチ", login_id="coach", sport="football") -> TestClient:
    client = make_client()
    res = client.post("/api/teams", json={"team_name": team_name, "name": name, "login_id": login_id, "password": "password123",
                                          "sport": sport})
    assert res.status_code == 200, res.text
    return client


def join(trainer: TestClient, name: str, login_id: str) -> TestClient:
    code = trainer.get("/api/admin/team").json()["invite_code"]
    client = make_client()
    res = client.post("/api/register", json={"invite_code": code, "name": name, "login_id": login_id, "password": "password123"})
    assert res.status_code == 200, res.text
    return client


def exercise_id(client: TestClient, name: str) -> int:
    return next(e["id"] for e in client.get("/api/exercises").json()["exercises"] if e["name"] == name)


def do_workout(client: TestClient, sets: list[tuple[str, float | None, int]]) -> int:
    """筋トレを 1回やって終わる。筋トレの番号を返す。"""
    workout = client.post("/api/workouts/start").json()["workout"]
    for name, weight, reps in sets:
        res = client.post(f"/api/workouts/{workout['id']}/sets",
                          json={"exercise_id": exercise_id(client, name), "weight": weight, "reps": reps})
        assert res.status_code == 200, res.text
    assert client.post(f"/api/workouts/{workout['id']}/finish").status_code == 200
    return workout["id"]
