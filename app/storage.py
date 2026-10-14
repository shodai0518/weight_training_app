"""写真の置き場。

- インターネット上（Render）：S3互換のストレージ（Cloudflare R2）。Render は 置いたファイルが 再起動で消えるため
- パソコンの中：フォルダ（data/photos）

設定（環境変数。4つとも入れると R2 を使う）:
- S3_ENDPOINT_URL：R2 のアドレス（https://＜アカウントID＞.r2.cloudflarestorage.com）
- S3_BUCKET：バケットの名前
- S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY：R2 の APIトークンで作った 鍵の組

写真は 非公開のまま置いて、ログインしている 同じチームの人にだけ、アプリを通して渡す。
ほかの場所は save / read / delete だけを使う。
"""

import logging
import os

from app.db import DATA_DIR

logger = logging.getLogger(__name__)

PHOTO_DIR = DATA_DIR / "photos"
S3_SETTINGS = ("S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")

_client = None


def s3_configured() -> bool:
    return all(os.getenv(name) for name in S3_SETTINGS)


def missing_s3_settings() -> list[str]:
    return [name for name in S3_SETTINGS if not os.getenv(name)]


def _s3():
    """R2 に つなぐ道具（1回だけ作って 使い回す）。テストでは 差しかえる。"""
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "s3",
            endpoint_url=os.getenv("S3_ENDPOINT_URL"),
            aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
            region_name=os.getenv("S3_REGION", "auto"),  # R2 は「auto」
            config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 3}),
        )
    return _client


def save(key: str, data: bytes) -> None:
    if s3_configured():
        _s3().put_object(Bucket=os.getenv("S3_BUCKET"), Key=key, Body=data, ContentType="image/jpeg")
        return
    path = PHOTO_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def read(key: str) -> bytes | None:
    if not key:
        return None
    if s3_configured():
        try:
            return _s3().get_object(Bucket=os.getenv("S3_BUCKET"), Key=key)["Body"].read()
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code not in ("NoSuchKey", "404"):
                logger.warning("写真を 読めませんでした（%s）", code or type(error).__name__)
            return None
    path = PHOTO_DIR / key
    return path.read_bytes() if path.is_file() else None


def delete(key: str) -> None:
    if not key:
        return
    if s3_configured():
        try:
            _s3().delete_object(Bucket=os.getenv("S3_BUCKET"), Key=key)
        except Exception as error:  # 消せなくても アプリは止めない（あとで 手で消せる）
            logger.warning("写真を 消せませんでした（%s）", type(error).__name__)
        return
    (PHOTO_DIR / key).unlink(missing_ok=True)
