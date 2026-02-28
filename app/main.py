from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.auth import get_session_user, hash_password, verify_password
from app.config import get_settings
from app.db import Database
from app.profiles import ProfileStore
from app.routes.api import router as api_router
from app.services.worker import JobManager
from app import repository


settings = get_settings()
templates = Jinja2Templates(directory=str(settings.templates_dir))


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    settings.ensure_directories()

    db = Database(settings.database_path)
    await db.connect()
    await repository.initialize_schema(db)
    await repository.reset_stuck_jobs(db)
    await repository.ensure_user(db, settings.admin_username, hash_password(settings.admin_password))

    profile_store = ProfileStore(settings.profiles_dir, settings.base_dir)
    profile_store.load()

    app.state.settings = settings
    app.state.db = db
    app.state.profile_store = profile_store

    job_manager = JobManager(app)
    app.state.job_manager = job_manager
    await job_manager.start_queued_jobs()

    try:
        yield
    finally:
        await job_manager.shutdown()
        await db.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
app.include_router(api_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login")
async def login_page(request: Request) -> Any:
    if get_session_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": "",
            "app_name": settings.app_name,
        },
    )


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> Any:
    db = request.app.state.db
    user = await repository.get_user_by_username(db, username.strip())
    if not user or not user.get("is_active"):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid username or password",
                "app_name": settings.app_name,
            },
            status_code=400,
        )

    if not verify_password(password, str(user["password_hash"])):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid username or password",
                "app_name": settings.app_name,
            },
            status_code=400,
        )

    request.session["user"] = str(user["username"])
    return RedirectResponse(url="/", status_code=303)


@app.get("/logout")
async def logout(request: Request) -> Any:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/")
async def index(request: Request) -> Any:
    user = get_session_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)

    defaults = {
        "default_http_concurrency": settings.default_http_concurrency,
        "default_browser_concurrency": settings.default_browser_concurrency,
        "default_max_retries": settings.default_max_retries,
        "default_request_delay_ms": settings.default_request_delay_ms,
    }

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "user": user,
            "defaults_json": json.dumps(defaults),
        },
    )
