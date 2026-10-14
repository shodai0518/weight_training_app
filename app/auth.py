"""ログイン。1人ずつのアカウントで、チームと役割（選手／トレーナー）を持つ。

設定（環境変数）:
- SECRET_KEY：ログインの印（クッキー）に サインをするための 鍵。なければ data/secret.key に作る（パソコンの中で試すとき用）
- OPERATOR_CODE：新しいチームを作るための 運営用の合言葉。入れなければ だれでもチームを作れる
"""

import hashlib
import hmac
import os
import secrets
import time

from fastapi import Cookie, Depends, HTTPException, Request, Response
from sqlmodel import Session

from app import config
from app.db import DATA_DIR, get_session
from app.models import User

COOKIE = "app_session"
_failures: dict[str, list[float]] = {}  # 接続元ごとの パスワードをまちがえた時刻
_secret: str | None = None


def secret_key() -> str:
    global _secret
    if _secret is None:
        _secret = os.getenv("SECRET_KEY")
        if not _secret:
            path = DATA_DIR / "secret.key"
            if not path.exists():
                path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            _secret = path.read_text(encoding="utf-8").strip()
    return _secret


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    except ValueError:
        return False
    return hmac.compare_digest(actual.hex(), digest)


def _sign(user: User, expires: int) -> str:
    # パスワードの一部も入れる：パスワードを変えると、前のログインは 使えなくなる
    message = f"{user.id}.{expires}.{user.password_hash[-16:]}"
    return hmac.new(secret_key().encode(), message.encode(), hashlib.sha256).hexdigest()


def set_login(request: Request, response: Response, user: User) -> None:
    expires = int(time.time()) + config.LOGIN_DAYS * 86400
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").startswith("https")
    response.set_cookie(COOKIE, f"{user.id}.{expires}.{_sign(user, expires)}", max_age=config.LOGIN_DAYS * 86400,
                        httponly=True, samesite="lax", secure=https)


def clear_login(response: Response) -> None:
    response.delete_cookie(COOKIE)


def check_lock(request: Request) -> tuple[str, list[float]]:
    """何度もまちがえた接続元は、しばらく受けつけない。"""
    client = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")
    recent = [t for t in _failures.get(client, []) if t > time.time() - config.LOGIN_LOCK_MINUTES * 60]
    if len(recent) >= config.LOGIN_MAX_FAILURES:
        raise HTTPException(429, f"まちがいが続いたので、{config.LOGIN_LOCK_MINUTES}分ほど待ってから試してね")
    return client, recent


def note_failure(client: str, recent: list[float]) -> None:
    _failures[client] = recent + [time.time()]


def clear_failures(client: str) -> None:
    _failures.pop(client, None)


def current_user(session: Session = Depends(get_session), app_session: str | None = Cookie(default=None)) -> User:
    """ログインしている人。チームは この人の team_id で決める（画面から チームの番号は 受け取らない）。"""
    parts = (app_session or "").split(".")
    if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) > time.time():
        user = session.get(User, int(parts[0]))
        if user and user.active and hmac.compare_digest(parts[2], _sign(user, int(parts[1]))):
            return user
    raise HTTPException(401, "ログインしてね")


def require_trainer(user: User = Depends(current_user)) -> User:
    if user.role != "trainer":
        raise HTTPException(403, "トレーナーだけが できます")
    return user
