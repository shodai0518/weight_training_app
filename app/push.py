"""プッシュ通知（アプリを閉じていても スマホに出る知らせ）。

- 休憩タイマー：休憩が始まったときに「終わる時刻」を受け取り、その時刻に サーバーから送る
- コメント：付いたときに すぐ送る

設定（環境変数）:
- VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY：送り主の鍵（scripts/make_vapid_keys.py で作る）。
  なければ data/vapid.json に作る（パソコンの中で試すとき用）
- VAPID_SUBJECT：送り主の連絡先（mailto:... か https://...）。なければ Render が付けるアドレス（RENDER_EXTERNAL_URL）
"""

import base64
import json
import logging
import os
import threading
from urllib.parse import urlparse

from sqlmodel import Session, select

from app import db
from app.models import PushSub

logger = logging.getLogger(__name__)

# 送り先として認める場所（Apple・Google・Mozilla・Microsoft の通知サーバー）。それ以外には 送らない
# （何でも受け付けると、サーバーが 知らない場所へ 送りに行かされてしまう）
PUSH_HOSTS = (".push.apple.com", "fcm.googleapis.com", ".push.services.mozilla.com", ".notify.windows.com")
MAX_SUBSCRIPTIONS_PER_USER = 5  # 1人が登録できる スマホ・ブラウザの数（多すぎたら 古いものから忘れる）
URGENT_TTL = 60  # 休憩おわり：1分たっても届かなければ もう送らない（遅れたら 意味がない）
NORMAL_TTL = 24 * 3600  # コメント・メニュー：1日は 届けようとする

_keys: dict | None = None
_rest_timers: dict[int, threading.Timer] = {}  # 人ごとの「休憩おわり」の予約
_lock = threading.Lock()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def keys() -> dict:
    """送り主の鍵（public は 画面に渡す）。"""
    global _keys
    if _keys is None:
        private, public = os.getenv("VAPID_PRIVATE_KEY"), os.getenv("VAPID_PUBLIC_KEY")
        if private and public:
            _keys = {"private": private, "public": public}
        else:
            path = db.DATA_DIR / "vapid.json"
            if not path.exists():
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric import ec

                key = ec.generate_private_key(ec.SECP256R1())
                raw_private = key.private_numbers().private_value.to_bytes(32, "big")
                raw_public = key.public_key().public_bytes(serialization.Encoding.X962,
                                                           serialization.PublicFormat.UncompressedPoint)
                path.write_text(json.dumps({"private": _b64(raw_private), "public": _b64(raw_public)}), encoding="utf-8")
            _keys = json.loads(path.read_text(encoding="utf-8"))
    return _keys


def public_key() -> str | None:
    try:
        return keys()["public"]
    except Exception:
        return None  # 鍵が作れないときは、プッシュ通知なしで動かす


def allowed_endpoint(endpoint: str) -> bool:
    """本物の通知サーバー（https）のアドレスか。"""
    url = urlparse(endpoint)
    host = url.hostname or ""
    return url.scheme == "https" and any(host == h.lstrip(".") or host.endswith(h) for h in PUSH_HOSTS)


def subject() -> str:
    """送り主の連絡先。iPhone（Apple）は ここが正しくないと 通知を受け付けない。"""
    return os.getenv("VAPID_SUBJECT") or os.getenv("RENDER_EXTERNAL_URL") or "mailto:admin@example.com"


def _webpush(sub: PushSub, payload: dict, urgent: bool) -> None:
    from pywebpush import webpush

    webpush(
        subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
        data=json.dumps(payload, ensure_ascii=False),
        vapid_private_key=keys()["private"],
        vapid_claims={"sub": subject()},
        ttl=URGENT_TTL if urgent else NORMAL_TTL,
        headers={"Urgency": "high" if urgent else "normal"},  # 休憩おわりは 急ぎで届けてもらう
        timeout=10,  # 通知サーバーが返事をしなくても、ずっと待たない
    )


def _deliver(user_id: int, payload: dict, urgent: bool = False, send=None) -> int:
    """その人の 送り先ぜんぶに送る。もう使われていない送り先は 忘れる。送れた数を返す。"""
    sent = 0
    with Session(db.engine) as session:
        for sub in session.exec(select(PushSub).where(PushSub.user_id == user_id)).all():
            try:
                (send or _webpush)(sub, payload, urgent)
                sent += 1
            except Exception as error:  # 送れなくても アプリは止めない
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status in (404, 410):  # スマホ側で 通知をやめた
                    session.delete(sub)
                else:
                    logger.warning("通知を送れませんでした（%s）", status or type(error).__name__)
        session.commit()
    return sent


def send(user_id: int, payload: dict) -> None:
    """すぐ送る（返事を待たせないように 別のスレッドで）。"""
    threading.Thread(target=_deliver, args=(user_id, payload), daemon=True).start()


def schedule_rest(user_id: int, seconds: float, payload: dict) -> None:
    """休憩が終わる時刻に送るように 予約する（前の予約は 取り消す）。"""
    cancel_rest(user_id)
    timer = threading.Timer(seconds, _fire_rest, args=(user_id, payload))
    timer.daemon = True
    with _lock:
        _rest_timers[user_id] = timer
    timer.start()


def _fire_rest(user_id: int, payload: dict) -> None:
    with _lock:
        _rest_timers.pop(user_id, None)
    try:
        _deliver(user_id, payload, urgent=True)
    except Exception:
        logger.exception("休憩おわりの通知で エラーが起きました")


def cancel_rest(user_id: int) -> None:
    with _lock:
        timer = _rest_timers.pop(user_id, None)
    if timer:
        timer.cancel()


def rest_scheduled(user_id: int) -> bool:
    return user_id in _rest_timers
