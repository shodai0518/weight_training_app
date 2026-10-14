"""インターネット上（Render）で動かすときの 確かめ。足りない設定があれば、起動を止めて 何が足りないかを ログに出す。

パソコンの中では 足りなくても動く（鍵は data/ に作り、写真は data/photos に置く）。
でも Render では data/ が 再起動のたびに消えるので、そのまま動くと「ログインが消える」「写真が消える」「通知が届かなくなる」。
"""

import os

from app import storage


def on_render() -> bool:
    """Render の上で動いているか（Render が 自動で RENDER=true を入れる）。"""
    return bool(os.getenv("RENDER"))


def missing_settings() -> list[str]:
    missing = []
    if not os.getenv("DATABASE_URL"):
        missing.append("DATABASE_URL（Neon の接続文字列）")
    if not os.getenv("SECRET_KEY"):
        missing.append("SECRET_KEY（render.yaml なら 自動で作られる）")
    missing += [f"{name}（Cloudflare R2）" for name in storage.missing_s3_settings()]
    if not (os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY")):
        missing.append("VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY（scripts/make_vapid_keys.py で作る）")
    return missing


def check_production() -> None:
    if not on_render():
        return
    missing = missing_settings()
    if missing:
        raise RuntimeError("設定が 足りないので 起動を止めました。Render の Environment に 入れてください：\n- " + "\n- ".join(missing))
