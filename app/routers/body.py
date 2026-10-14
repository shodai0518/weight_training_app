"""体重・体脂肪率の記録。見えるのは 自分とトレーナーだけ（筋トレの記録とちがって、みんなには見せない）。"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app import auth, clock, config
from app.db import get_session
from app.models import BodyLog, User

router = APIRouter(prefix="/api")


class BodyInput(BaseModel):
    weight: float | None = None
    body_fat: float | None = None


def body_logs(session: Session, user_id: int) -> list[dict]:
    """古い順（グラフに そのまま使える）。"""
    since = clock.today() - timedelta(days=config.GRAPH_DAYS)
    rows = session.exec(select(BodyLog).where(BodyLog.user_id == user_id, BodyLog.day >= since).order_by(BodyLog.day)).all()
    return [{"id": r.id, "day": r.day.isoformat(), "weight": r.weight, "body_fat": r.body_fat} for r in rows]


def save_body(session: Session, user: User, weight: float | None, body_fat: float | None) -> None:
    """今日の分として記録する。今日の分が もうあれば、入れた項目だけ 上書きする。"""
    if weight is None and body_fat is None:
        raise HTTPException(400, "体重か 体脂肪率を入れてね")
    if weight is not None and not config.BODY_WEIGHT_MIN <= weight <= config.BODY_WEIGHT_MAX:
        raise HTTPException(400, "体重を確かめてね")
    if body_fat is not None and not config.BODY_FAT_MIN <= body_fat <= config.BODY_FAT_MAX:
        raise HTTPException(400, "体脂肪率を確かめてね")
    today = clock.today()
    row = session.exec(select(BodyLog).where(BodyLog.user_id == user.id, BodyLog.day == today)).first()
    if not row:
        row = BodyLog(user_id=user.id, day=today)
        session.add(row)
    if weight is not None:
        row.weight = round(weight, 1)
    if body_fat is not None:
        row.body_fat = round(body_fat, 1)


@router.get("/body")
def get_body(user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    return {"logs": body_logs(session, user.id)}


@router.put("/body")
def put_body(body: BodyInput, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    save_body(session, user, body.weight, body.body_fat)
    session.commit()
    return {"logs": body_logs(session, user.id)}


@router.delete("/body/{log_id}")
def delete_body(log_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    row = session.get(BodyLog, log_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "記録が見つかりません")
    session.delete(row)
    session.commit()
    return {"logs": body_logs(session, user.id)}
