"""インターネット上に置く準備：写真の置き場（R2）・Render での 設定の確かめ・render.yaml。"""

import base64
from pathlib import Path

import pytest
import yaml

from app import checks, storage
from tests.conftest import create_team, do_workout, join

JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 200).decode()
ROOT = Path(__file__).resolve().parent.parent


class FakeS3:
    """R2（S3）の まね。置いた写真を 辞書に持つ。"""

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        assert Bucket == "kintore-photos" and ContentType == "image/jpeg"
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            error = Exception("NoSuchKey")
            error.response = {"Error": {"Code": "NoSuchKey"}}
            raise error
        body = self.objects[Key]
        return {"Body": type("Body", (), {"read": lambda self: body})()}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


@pytest.fixture
def r2(monkeypatch):
    fake = FakeS3()
    for name, value in (("S3_ENDPOINT_URL", "https://example.r2.cloudflarestorage.com"), ("S3_BUCKET", "kintore-photos"),
                        ("S3_ACCESS_KEY_ID", "id"), ("S3_SECRET_ACCESS_KEY", "secret")):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(storage, "_client", fake)
    return fake


def test_photos_go_to_r2_when_configured(r2):
    coach = create_team()
    player = join(coach, "たなか", "tanaka")
    post = player.post("/api/posts", json={"workout_id": do_workout(player, [("懸垂", None, 10)]), "photos": [JPEG]}).json()
    assert len(r2.objects) == 1 and not storage.PHOTO_DIR.exists()  # パソコンのフォルダには 置かない
    photo = coach.get(post["photos"][0]["url"])
    assert photo.status_code == 200 and photo.content.startswith(b"\xff\xd8\xff")
    menu = coach.post("/api/admin/menus", json={"title": "写真", "photos": [JPEG]}).json()
    assert len(r2.objects) == 2
    coach.delete(f"/api/admin/menus/{menu['id']}")
    player.delete(f"/api/posts/{post['id']}")
    assert r2.objects == {}  # 消すと R2 からも消える


def test_missing_r2_object_is_404(r2):
    assert storage.read("1/1/none.jpg") is None


def test_production_needs_all_settings(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    checks.check_production()  # パソコンの中では 何もしない
    monkeypatch.setenv("RENDER", "true")
    for name in ("DATABASE_URL", "SECRET_KEY", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", *storage.S3_SETTINGS):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError) as error:
        checks.check_production()
    message = str(error.value)
    assert "DATABASE_URL" in message and "S3_BUCKET" in message and "VAPID_PUBLIC_KEY" in message
    for name in ("DATABASE_URL", "SECRET_KEY", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", *storage.S3_SETTINGS):
        monkeypatch.setenv(name, "x")
    checks.check_production()


def test_render_yaml_has_every_needed_setting():
    service = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))["services"][0]
    keys = {item["key"] for item in service["envVars"]}
    needed = {"DATABASE_URL", "SECRET_KEY", "OPERATOR_CODE", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", *storage.S3_SETTINGS}
    assert needed <= keys
    assert service["healthCheckPath"] == "/healthz" and "app.main:app" in service["startCommand"]
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for package in ("fastapi", "uvicorn", "sqlmodel", "psycopg", "pywebpush", "boto3"):
        assert package in requirements
