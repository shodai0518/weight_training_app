"""アプリの入口。"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import checks, db
from app.routers import admin, body, menus, posts, users, workouts

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    checks.check_production()  # Render で 設定が足りなければ、ここで止める
    db.init_db()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
for module in (users, workouts, posts, body, menus, admin):
    app.include_router(module.router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.middleware("http")
async def no_cache_for_app_files(request, call_next):
    """画面のファイルは 毎回 新しいかを確かめさせる（直したのに 古い画面が出る、を防ぐ）。"""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
