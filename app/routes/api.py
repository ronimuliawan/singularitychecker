from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response

from app.auth import require_api_user
from app import repository
from app.services.code_input import collect_codes


ALLOWED_RESULT_STATUSES = {
    "pending",
    "running",
    "queued_browser",
    "valid",
    "invalid",
    "unknown",
    "blocked",
    "error",
}


router = APIRouter(prefix="/api", tags=["api"])


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


@router.get("/profiles")
async def get_profiles(request: Request, _: str = Depends(require_api_user)) -> dict[str, Any]:
    profile_store = request.app.state.profile_store
    return {"profiles": profile_store.all_public()}


@router.post("/profiles/reload")
async def reload_profiles(request: Request, _: str = Depends(require_api_user)) -> dict[str, str]:
    profile_store = request.app.state.profile_store
    profile_store.load()
    return {"message": "Profiles reloaded"}


@router.post("/profiles/{profile_name}/session-state")
async def upload_session_state(
    request: Request,
    profile_name: str,
    session_file: UploadFile = File(...),
    _: str = Depends(require_api_user),
) -> dict[str, Any]:
    profile_store = request.app.state.profile_store
    profile = profile_store.get(profile_name)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    body = await session_file.read()
    if len(body) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Session file too large")

    try:
        payload = json.loads(body.decode("utf-8", errors="ignore"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Session state must be a JSON object")

    target_path = profile_store.resolve_storage_state_path(profile_name)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(payload), encoding="utf-8")

    return {
        "message": "Session state uploaded",
        "profile": profile_name,
        "path": str(target_path),
    }


@router.post("/jobs")
async def create_job(
    request: Request,
    profile_name: str = Form(...),
    redeem_url_override: str = Form(""),
    pasted_codes: str = Form(""),
    http_concurrency: int = Form(20),
    browser_concurrency: int = Form(1),
    max_retries: int = Form(2),
    request_delay_ms: int = Form(100),
    code_files: list[UploadFile] | None = File(default=None),
    current_user: str = Depends(require_api_user),
) -> dict[str, Any]:
    profile_store = request.app.state.profile_store
    profile = profile_store.get(profile_name)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    redeem_url_override = redeem_url_override.strip()
    if redeem_url_override and "{code}" not in redeem_url_override:
        raise HTTPException(status_code=400, detail="Redeem URL override must include {code}")

    collection = await collect_codes(pasted_codes, code_files or [])
    if not collection.codes:
        raise HTTPException(status_code=400, detail="No redeem codes were provided")

    http_concurrency = _clamp(http_concurrency, 1, 200)
    browser_concurrency = _clamp(browser_concurrency, 0, 20)
    max_retries = _clamp(max_retries, 0, 10)
    request_delay_ms = _clamp(request_delay_ms, 0, 5000)

    job_id = uuid.uuid4().hex
    db = request.app.state.db
    await repository.create_job_with_codes(
        db,
        job_id=job_id,
        profile_name=profile_name,
        redeem_url_override=redeem_url_override or None,
        created_by=current_user,
        codes=collection.codes,
        http_concurrency=http_concurrency,
        browser_concurrency=browser_concurrency,
        max_retries=max_retries,
        request_delay_ms=request_delay_ms,
    )

    request.app.state.job_manager.start_job(job_id)

    return {
        "job_id": job_id,
        "profile_name": profile_name,
        "total_codes": collection.unique_count,
        "raw_codes": collection.raw_count,
        "duplicates_removed": collection.duplicates_removed,
        "status": "queued",
    }


@router.get("/jobs")
async def get_jobs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    _: str = Depends(require_api_user),
) -> dict[str, Any]:
    db = request.app.state.db
    jobs = await repository.list_jobs(db, limit=limit)
    return {"jobs": jobs}


@router.get("/jobs/{job_id}")
async def get_job_detail(
    request: Request,
    job_id: str,
    _: str = Depends(require_api_user),
) -> dict[str, Any]:
    db = request.app.state.db
    job = await repository.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    counts = await repository.get_job_counts(db, job_id)
    return {"job": job, "counts": counts}


@router.get("/jobs/{job_id}/results")
async def get_job_results(
    request: Request,
    job_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status"),
    _: str = Depends(require_api_user),
) -> dict[str, Any]:
    if status_filter and status_filter not in ALLOWED_RESULT_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status filter")

    db = request.app.state.db
    job = await repository.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    rows = await repository.list_results(
        db,
        job_id,
        limit=limit,
        offset=offset,
        status=status_filter,
    )
    return {"results": rows}


@router.get("/jobs/{job_id}/export.csv")
async def export_job_results_csv(
    request: Request,
    job_id: str,
    _: str = Depends(require_api_user),
) -> Response:
    db = request.app.state.db
    job = await repository.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    rows = await repository.list_results_for_export(db, job_id)
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(
        ["code", "status", "source", "reason", "attempts", "http_status", "redirect_url", "checked_at"]
    )
    for row in rows:
        writer.writerow(
            [
                row["code"],
                row["status"],
                row["source"],
                row["reason"],
                row["attempts"],
                row["http_status"],
                row["redirect_url"],
                row["checked_at"],
            ]
        )

    return Response(
        content=stream.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=job_{job_id}.csv"},
    )


@router.post("/jobs/{job_id}/rerun-uncertain")
async def rerun_uncertain(
    request: Request,
    job_id: str,
    _: str = Depends(require_api_user),
) -> dict[str, Any]:
    db = request.app.state.db
    job = await repository.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "running":
        raise HTTPException(status_code=409, detail="Job is currently running")

    changed = await repository.rerun_uncertain_results(db, job_id)
    if changed == 0:
        await repository.mark_job_completed(db, job_id)
        return {"message": "No unknown/blocked/error results to rerun", "updated": 0}

    request.app.state.job_manager.start_job(job_id)
    return {"message": "Rerun started", "updated": changed}
