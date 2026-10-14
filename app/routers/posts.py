"""タイムライン・投稿・写真・コメント・スタンプ。"""

import base64
import binascii
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from app import auth, clock, config, push, services, storage
from app.db import get_session
from app.models import Comment, Notice, Post, PostPhoto, Reaction, User, Workout

router = APIRouter(prefix="/api")


class PostCreate(BaseModel):
    workout_id: int
    body: str = ""
    photos: list[str] = []  # 画面で小さくした JPEG（base64）


class PostUpdate(BaseModel):
    body: str


class PhotosAdd(BaseModel):
    photos: list[str]


class CommentCreate(BaseModel):
    body: str
    parent_id: int | None = None  # 返信するときの 元のコメント


class ReactionSet(BaseModel):
    emoji: str


def decode_photo(data: str) -> bytes:
    if "," in data[:64]:  # data:image/jpeg;base64,... の前の部分を取る
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "写真が読めませんでした")
    if len(raw) > config.PHOTO_MAX_BYTES:
        raise HTTPException(413, "写真が大きすぎます")
    if not raw.startswith(b"\xff\xd8\xff"):  # JPEG だけ受け取る（画面で JPEG にしてから送る）
        raise HTTPException(400, "写真が読めませんでした")
    return raw


def notify(session: Session, post: Post, actor: User, kind: str) -> None:
    """投稿した人に 知らせる（自分で自分には 知らせない）。"""
    if post.user_id == actor.id:
        return
    if kind == "reaction":  # スタンプは 1つの投稿に 知らせ 1つ。新しく付くたびに「まだ読んでいない」にもどして 一番上へ
        notice = session.exec(select(Notice).where(Notice.user_id == post.user_id, Notice.post_id == post.id,
                                                   Notice.kind == "reaction")).first()
        if notice:
            session.delete(notice)  # 作り直す（番号が新しくなって 一覧の 一番上に来る）
            session.flush()
    session.add(Notice(user_id=post.user_id, kind=kind, post_id=post.id, actor_id=actor.id))


@router.get("/timeline")
def timeline(before: int | None = None, user_id: int | None = None,
             user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    services.expire_photos(session)  # 保存期間をすぎた写真を片づける（1日に1回だけ 動く）
    query = select(Post).where(Post.team_id == user.team_id)
    if before:
        query = query.where(Post.id < before)
    if user_id:
        query = query.where(Post.user_id == user_id)
    posts = session.exec(query.order_by(Post.id.desc()).limit(config.TIMELINE_PAGE + 1)).all()
    more = len(posts) > config.TIMELINE_PAGE
    return {"posts": services.serialize_posts(session, posts[:config.TIMELINE_PAGE], user), "more": more}


@router.get("/posts/{post_id}")
def get_post(post_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    post = services.get_team_post(session, user, post_id)
    return services.serialize_posts(session, [post], user)[0]


@router.post("/posts")
def create_post(body: PostCreate, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    workout = session.get(Workout, body.workout_id)
    if not workout or workout.user_id != user.id:
        raise HTTPException(404, "筋トレが見つかりません")
    if workout.status != "done":
        raise HTTPException(409, "筋トレを終わってから 投稿してね")
    if session.exec(select(Post).where(Post.workout_id == workout.id)).first():
        raise HTTPException(409, "この筋トレは もう投稿しています")
    text = body.body.strip()
    if len(text) > config.POST_BODY_MAX:
        raise HTTPException(400, f"ひとことは {config.POST_BODY_MAX}文字までです")
    if len(body.photos) > config.POST_MAX_PHOTOS:
        raise HTTPException(400, f"写真は {config.POST_MAX_PHOTOS}枚までです")
    images = [decode_photo(p) for p in body.photos]

    post = Post(team_id=user.team_id, workout_id=workout.id, user_id=user.id, body=text)
    session.add(post)
    session.flush()
    for position, image in enumerate(images):
        key = f"{user.team_id}/{post.id}/{uuid.uuid4().hex}.jpg"
        storage.save(key, image)
        session.add(PostPhoto(post_id=post.id, storage_key=key, position=position))
    session.commit()
    return services.serialize_posts(session, [post], user)[0]


@router.patch("/posts/{post_id}")
def update_post(post_id: int, body: PostUpdate, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    post = services.get_team_post(session, user, post_id)
    if post.user_id != user.id:
        raise HTTPException(403, "自分の投稿だけ 直せます")
    text = body.body.strip()
    if len(text) > config.POST_BODY_MAX:
        raise HTTPException(400, f"ひとことは {config.POST_BODY_MAX}文字までです")
    post.body = text
    session.commit()
    return services.serialize_posts(session, [post], user)[0]


def own_post(session: Session, user: User, post_id: int) -> Post:
    post = services.get_team_post(session, user, post_id)
    if post.user_id != user.id:
        raise HTTPException(403, "自分の投稿だけ 直せます")
    return post


@router.post("/posts/{post_id}/photos")
def add_photos(post_id: int, body: PhotosAdd, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """投稿したあとで 写真を足す（あわせて 4枚まで）。"""
    post = own_post(session, user, post_id)
    rows = session.exec(select(PostPhoto).where(PostPhoto.post_id == post.id, PostPhoto.storage_key != "")).all()
    if not body.photos or len(rows) + len(body.photos) > config.POST_MAX_PHOTOS:
        raise HTTPException(400, f"写真は あわせて {config.POST_MAX_PHOTOS}枚までです")
    images = [decode_photo(p) for p in body.photos]
    position = max((r.position for r in rows), default=-1) + 1
    for offset, image in enumerate(images):
        key = f"{user.team_id}/{post.id}/{uuid.uuid4().hex}.jpg"
        storage.save(key, image)
        session.add(PostPhoto(post_id=post.id, storage_key=key, position=position + offset))
    session.commit()
    return services.serialize_posts(session, [post], user)[0]


@router.delete("/posts/{post_id}/photos/{photo_id}")
def delete_photo(post_id: int, photo_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """投稿したあとで 写真を外す。"""
    post = own_post(session, user, post_id)
    photo = session.get(PostPhoto, photo_id)
    if not photo or photo.post_id != post.id:
        raise HTTPException(404, "写真が見つかりません")
    if photo.storage_key:
        storage.delete(photo.storage_key)
    session.delete(photo)
    session.commit()
    return services.serialize_posts(session, [post], user)[0]


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    post = services.get_team_post(session, user, post_id)
    if post.user_id != user.id and user.role != "trainer":
        raise HTTPException(403, "自分の投稿だけ 消せます")
    services.delete_post(session, post)
    session.commit()
    return {"ok": True}


@router.get("/photos/{photo_id}")
def get_photo(photo_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """写真は ログインしている 同じチームの人にだけ渡す。"""
    photo = session.get(PostPhoto, photo_id)
    if photo:
        services.get_team_post(session, user, photo.post_id)
    data = storage.read(photo.storage_key) if photo and photo.storage_key else None
    if not data:
        raise HTTPException(404, "写真が見つかりません")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# ---- コメント ----

@router.post("/posts/{post_id}/comments")
def add_comment(post_id: int, body: CommentCreate, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    post = services.get_team_post(session, user, post_id)
    text = body.body.strip()
    if not text or len(text) > config.COMMENT_MAX:
        raise HTTPException(400, f"コメントは 1〜{config.COMMENT_MAX}文字で入れてね")
    parent = None
    if body.parent_id is not None:
        parent = session.get(Comment, body.parent_id)
        if not parent or parent.post_id != post.id:
            raise HTTPException(404, "返信するコメントが見つかりません")
        if parent.parent_id is not None:  # 返信への返信は、同じ元のコメントに付ける（段は 1つだけ）
            parent = session.get(Comment, parent.parent_id)
    session.add(Comment(post_id=post.id, user_id=user.id, body=text, parent_id=parent.id if parent else None))
    trainer = user.role == "trainer"
    if trainer and post.user_id != user.id and not post.reviewed_at:  # トレーナーがコメントしたら「見た」ことにする
        post.reviewed_by, post.reviewed_at = user.id, clock.now()
    notify(session, post, user, "trainer_comment" if trainer else "comment")
    # 返信された人にも知らせる（投稿した人には 上で知らせたので、重ねない）
    reply_to = parent.user_id if parent and parent.user_id not in (user.id, post.user_id) else None
    if reply_to:
        session.add(Notice(user_id=reply_to, kind="reply", post_id=post.id, actor_id=user.id))
    session.commit()
    if post.user_id != user.id:
        title = f"トレーナーの {user.name} さんから コメント" if trainer else f"{user.name} さんから コメント"
        push.send(post.user_id, {"title": title, "body": text[:80], "tag": f"post-{post.id}", "url": f"/?post={post.id}"})
    if reply_to:
        push.send(reply_to, {"title": f"{user.name} さんから 返信", "body": text[:80], "tag": f"post-{post.id}", "url": f"/?post={post.id}"})
    return services.serialize_posts(session, [post], user)[0]


def team_comment(session: Session, user: User, comment_id: int) -> tuple[Comment, Post]:
    comment = session.get(Comment, comment_id)
    if not comment:
        raise HTTPException(404, "コメントが見つかりません")
    return comment, services.get_team_post(session, user, comment.post_id)


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    comment, post = team_comment(session, user, comment_id)
    if comment.user_id != user.id and user.role != "trainer":
        raise HTTPException(403, "自分のコメントだけ 消せます")
    # 返信も一緒に消す（スタンプ → 返信 → 元のコメント の順）
    replies = session.exec(select(Comment).where(Comment.parent_id == comment.id)).all()
    for r in session.exec(select(Reaction).where(Reaction.comment_id.in_([comment.id] + [c.id for c in replies]))).all():
        session.delete(r)
    session.flush()
    for reply in replies:
        session.delete(reply)
    session.flush()
    session.delete(comment)
    session.commit()
    return services.serialize_posts(session, [post], user)[0]


# ---- スタンプ ----

def set_reaction(session: Session, user: User, emoji: str | None, post_id: int | None = None, comment_id: int | None = None) -> None:
    """スタンプを付ける（別のを選ぶと 入れかわる）。emoji が None なら 外す。"""
    if emoji is not None and emoji not in config.REACTIONS:
        raise HTTPException(400, "そのスタンプは 使えません")
    existing = session.exec(select(Reaction).where(
        Reaction.user_id == user.id, Reaction.post_id == post_id, Reaction.comment_id == comment_id)).first()
    if emoji is None:
        if existing:
            session.delete(existing)
    elif existing:
        existing.emoji = emoji
    else:
        session.add(Reaction(user_id=user.id, post_id=post_id, comment_id=comment_id, emoji=emoji))


@router.put("/posts/{post_id}/reaction")
def react_post(post_id: int, body: ReactionSet, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    post = services.get_team_post(session, user, post_id)
    set_reaction(session, user, body.emoji, post_id=post.id)
    notify(session, post, user, "reaction")
    session.commit()
    return services.serialize_posts(session, [post], user)[0]


@router.delete("/posts/{post_id}/reaction")
def unreact_post(post_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    post = services.get_team_post(session, user, post_id)
    set_reaction(session, user, None, post_id=post.id)
    session.commit()
    return services.serialize_posts(session, [post], user)[0]


@router.put("/comments/{comment_id}/reaction")
def react_comment(comment_id: int, body: ReactionSet, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    comment, post = team_comment(session, user, comment_id)
    set_reaction(session, user, body.emoji, comment_id=comment.id)
    session.commit()
    return services.serialize_posts(session, [post], user)[0]


@router.delete("/comments/{comment_id}/reaction")
def unreact_comment(comment_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    comment, post = team_comment(session, user, comment_id)
    set_reaction(session, user, None, comment_id=comment.id)
    session.commit()
    return services.serialize_posts(session, [post], user)[0]
