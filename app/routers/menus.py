"""トレーナーからのメニュー。トレーナーが配って、みんな（または 選んだ人）が それを開いて 記録する。"""

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app import auth, clock, config, push, services, storage
from app.db import get_session
from app.models import Menu, MenuItem, MenuPhoto, MenuTarget, User
from app.routers.posts import decode_photo

router = APIRouter(prefix="/api")


class MenuItemInput(BaseModel):
    exercise_id: int
    sets: int = 1
    reps: int | None = None  # 回数（距離×本数なら 本数）
    weight: float | None = None
    seconds: int | None = None
    distance_m: float | None = None
    minutes: float | None = None  # 有酸素
    distance_km: float | None = None  # 有酸素


class MenuCreate(BaseModel):
    title: str
    note: str = ""  # トレーナーからのコメント
    start_day: date | None = None  # いつから
    end_day: date | None = None  # いつまで
    items: list[MenuItemInput] = []
    photos: list[str] = []  # 画面で小さくした JPEG（base64）。紙やスプレッドシートの写真
    targets: list[int] = []  # 配る相手（空なら チーム全員）


def active_menus(session: Session, user: User) -> list[Menu]:
    return list(session.exec(select(Menu).where(Menu.team_id == user.team_id, Menu.active == True).order_by(Menu.id.desc())).all())  # noqa: E712


@router.get("/menus")
def list_menus(user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """自分に配られているメニュー。期間がすぎても 少しの間（MENU_KEEP_DAYS_AFTER_END 日）は残す。

    並び：今の期間 → これから → 期間おわり。同じ中では 新しい順。
    """
    oldest_end = clock.today() - timedelta(days=config.MENU_KEEP_DAYS_AFTER_END)
    menus = active_menus(session, user)
    targets = services.menu_targets(session, [m.id for m in menus])
    menus = [m for m in menus if (not m.end_day or m.end_day >= oldest_end) and services.menu_for(m, user, targets)]
    order = ["now", "soon", "over"]
    menus.sort(key=lambda m: (order.index(services.menu_status(m)), -m.id))
    return services.menu_dicts(session, menus, services.exercise_map(session, user.team_id))


@router.get("/admin/menus")
def list_all_menus(user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    """トレーナー用：配っているメニューを全部（期間が終わったものも。配る相手も付ける）。"""
    return services.menu_dicts(session, active_menus(session, user), services.exercise_map(session, user.team_id), with_targets=True)


def check_item(item: MenuItemInput, kind: str) -> MenuItem:
    """記録の仕方に合わせて 目標を確かめる。"""
    def need(value, low, high, message):
        if value is None or not low <= value <= high:
            raise HTTPException(400, message)
        return value

    row = MenuItem(menu_id=0, exercise_id=item.exercise_id, sets=1, reps=0)
    if kind == "cardio":
        if not (item.minutes or item.distance_km):
            raise HTTPException(400, "有酸素は 時間か 距離を入れてね")
        if not 0 <= (item.minutes or 0) <= 600 or not 0 <= (item.distance_km or 0) <= 300:
            raise HTTPException(400, "有酸素の 数字を確かめてね")
        row.minutes, row.distance_km = item.minutes or None, item.distance_km or None
        return row
    row.sets = need(item.sets, 1, config.MENU_MAX_SETS, "セット数を確かめてね")
    if kind == "time":
        row.seconds = need(item.seconds, 1, 3600, "時間（秒）を入れてね")
    elif kind == "minutes":
        row.minutes = need(item.minutes, 1, 600, "時間（分）を入れてね")
    elif kind == "count":
        row.reps = need(item.reps, 1, 999, "本数を入れてね")
    elif kind == "distance_sets":
        row.distance_m = need(item.distance_m, 1, 5000, "距離（m）を入れてね")
        row.reps = need(item.reps, 1, 99, "本数を入れてね")
    else:
        row.reps = need(item.reps, 1, 999, "回数を入れてね")
        if kind == "weight_reps":
            if item.weight is not None and not 0 <= item.weight <= 999:
                raise HTTPException(400, "重さを確かめてね")
            row.weight = item.weight or None
    return row


@router.post("/admin/menus")
def create_menu(body: MenuCreate, user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    title, note = body.title.strip(), body.note.strip()
    if not title or len(title) > config.MENU_TITLE_MAX:
        raise HTTPException(400, f"メニューの名前は 1〜{config.MENU_TITLE_MAX}文字で入れてね")
    if len(note) > config.MENU_NOTE_MAX:
        raise HTTPException(400, f"コメントは {config.MENU_NOTE_MAX}文字までです")
    if body.start_day and body.end_day and body.end_day < body.start_day:
        raise HTTPException(400, "期間の 終わりの日が、始まりの日より前になっています")
    if not body.items and not body.photos:
        raise HTTPException(400, "写真か 種目を 1つ以上 入れてね")
    if len(body.items) > config.MENU_MAX_ITEMS:
        raise HTTPException(400, f"種目は {config.MENU_MAX_ITEMS}個までです")
    if len(body.photos) > config.MENU_MAX_PHOTOS:
        raise HTTPException(400, f"写真は {config.MENU_MAX_PHOTOS}枚までです")
    images = [decode_photo(p) for p in body.photos]
    exercises = services.exercise_map(session, user.team_id)
    rows = []
    for item in body.items:
        exercise = exercises.get(item.exercise_id)
        if not exercise:
            raise HTTPException(404, "種目が見つかりません")
        rows.append(check_item(item, services.kind_of(exercise)))
    members = {u.id: u for u in services.team_users(session, user.team_id).values() if u.active}
    targets = sorted(set(body.targets))
    if any(t not in members for t in targets):
        raise HTTPException(404, "配る相手が 見つかりません")
    count = session.exec(select(func.count()).select_from(Menu).where(Menu.team_id == user.team_id, Menu.active == True)).one()  # noqa: E712
    if count >= config.MENU_MAX_ACTIVE:
        raise HTTPException(409, f"配れるメニューは {config.MENU_MAX_ACTIVE}個までです。古いメニューの配信を やめてね")

    menu = Menu(team_id=user.team_id, title=title, note=note, start_day=body.start_day, end_day=body.end_day, created_by=user.id)
    session.add(menu)
    session.flush()
    for position, row in enumerate(rows):
        row.menu_id, row.position = menu.id, position
        session.add(row)
    for target in targets:
        session.add(MenuTarget(menu_id=menu.id, user_id=target))
    for position, image in enumerate(images):
        key = f"{user.team_id}/menu/{menu.id}/{uuid.uuid4().hex}.jpg"
        storage.save(key, image)
        session.add(MenuPhoto(menu_id=menu.id, storage_key=key, position=position))
    session.commit()
    # 配る相手（選んでいなければ チームのみんな）に 知らせる。通知をオンにしている人のスマホに出る
    for member in (members[t] for t in targets) if targets else members.values():
        if member.id != user.id:
            push.send(member.id, {"title": f"新しいメニュー：{title}", "body": note[:80] or f"{user.name} さんから", "tag": f"menu-{menu.id}", "url": "/?menu=1"})
    return {"id": menu.id}


@router.get("/menu-photos/{photo_id}")
def get_menu_photo(photo_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """メニューの写真は ログインしている 同じチームの人にだけ渡す。"""
    photo = session.get(MenuPhoto, photo_id)
    menu = session.get(Menu, photo.menu_id) if photo else None
    data = storage.read(photo.storage_key) if menu and menu.team_id == user.team_id else None
    if not data:
        raise HTTPException(404, "写真が見つかりません")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/admin/menus/{menu_id}")
def stop_menu(menu_id: int, user: User = Depends(auth.require_trainer), session: Session = Depends(get_session)):
    """配るのをやめる（そのメニューでやった記録は 残る。写真は もう使わないので 消す）。"""
    menu = session.get(Menu, menu_id)
    if not menu or menu.team_id != user.team_id:
        raise HTTPException(404, "メニューが見つかりません")
    menu.active = False
    for photo in session.exec(select(MenuPhoto).where(MenuPhoto.menu_id == menu.id)).all():
        storage.delete(photo.storage_key)
        session.delete(photo)
    session.commit()
    return {"ok": True}
