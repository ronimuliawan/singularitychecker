from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import Database


FINAL_RESULT_STATUSES = {"valid", "invalid", "unknown", "blocked", "error"}


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def initialize_schema(db: Database) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            profile_name TEXT NOT NULL,
            redeem_url_override TEXT,
            created_by TEXT NOT NULL,
            status TEXT NOT NULL,
            total_codes INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            http_concurrency INTEGER NOT NULL,
            browser_concurrency INTEGER NOT NULL,
            max_retries INTEGER NOT NULL,
            request_delay_ms INTEGER NOT NULL,
            notes TEXT
        )
        """
    )

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            code TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            reason TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            http_status INTEGER,
            redirect_url TEXT,
            checked_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(job_id, code),
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )

    await db.execute("CREATE INDEX IF NOT EXISTS idx_results_job_id ON results(job_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_results_job_status ON results(job_id, status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")


async def ensure_user(db: Database, username: str, password_hash: str) -> None:
    existing = await db.fetchone("SELECT id FROM users WHERE username = ?", (username,))
    if existing is not None:
        return

    await db.execute(
        "INSERT INTO users(username, password_hash, is_active, created_at) VALUES (?, ?, 1, ?)",
        (username, password_hash, utc_now()),
    )


async def get_user_by_username(db: Database, username: str) -> dict[str, Any] | None:
    return await db.fetchone(
        "SELECT id, username, password_hash, is_active, created_at FROM users WHERE username = ?",
        (username,),
    )


async def create_job_with_codes(
    db: Database,
    *,
    job_id: str,
    profile_name: str,
    redeem_url_override: str | None,
    created_by: str,
    codes: list[str],
    http_concurrency: int,
    browser_concurrency: int,
    max_retries: int,
    request_delay_ms: int,
) -> None:
    now = utc_now()
    total_codes = len(codes)

    if db.conn is None:
        raise RuntimeError("Database is not connected")

    await db.conn.execute("BEGIN")
    try:
        await db.conn.execute(
            """
            INSERT INTO jobs(
                id, profile_name, redeem_url_override, created_by, status,
                total_codes, created_at, started_at, completed_at,
                http_concurrency, browser_concurrency, max_retries, request_delay_ms, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, NULL)
            """,
            (
                job_id,
                profile_name,
                redeem_url_override,
                created_by,
                "queued",
                total_codes,
                now,
                http_concurrency,
                browser_concurrency,
                max_retries,
                request_delay_ms,
            ),
        )

        rows = [
            (
                job_id,
                code,
                "pending",
                "none",
                None,
                0,
                None,
                None,
                None,
                now,
                now,
            )
            for code in codes
        ]
        await db.conn.executemany(
            """
            INSERT INTO results(
                job_id, code, status, source, reason, attempts,
                http_status, redirect_url, checked_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

        await db.conn.commit()
    except Exception:
        await db.conn.rollback()
        raise


async def list_jobs(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    return await db.fetchall(
        """
        SELECT id, profile_name, created_by, status, total_codes, created_at, started_at, completed_at
        FROM jobs
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )


async def list_jobs_by_status(db: Database, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
    placeholders = ", ".join(["?"] * len(statuses))
    return await db.fetchall(
        f"""
        SELECT id, profile_name, created_by, status, total_codes, created_at, started_at, completed_at,
               http_concurrency, browser_concurrency, max_retries, request_delay_ms, redeem_url_override
        FROM jobs
        WHERE status IN ({placeholders})
        ORDER BY created_at ASC
        """,
        statuses,
    )


async def get_job(db: Database, job_id: str) -> dict[str, Any] | None:
    return await db.fetchone(
        """
        SELECT id, profile_name, redeem_url_override, created_by, status, total_codes,
               created_at, started_at, completed_at, http_concurrency, browser_concurrency,
               max_retries, request_delay_ms, notes
        FROM jobs
        WHERE id = ?
        """,
        (job_id,),
    )


async def mark_job_running(db: Database, job_id: str) -> None:
    await db.execute(
        "UPDATE jobs SET status = ?, started_at = ?, completed_at = NULL, notes = NULL WHERE id = ?",
        ("running", utc_now(), job_id),
    )


async def mark_job_completed(db: Database, job_id: str) -> None:
    await db.execute(
        "UPDATE jobs SET status = ?, completed_at = ? WHERE id = ?",
        ("completed", utc_now(), job_id),
    )


async def mark_job_failed(db: Database, job_id: str, note: str) -> None:
    await db.execute(
        "UPDATE jobs SET status = ?, completed_at = ?, notes = ? WHERE id = ?",
        ("failed", utc_now(), note[:500], job_id),
    )


async def reset_stuck_jobs(db: Database) -> None:
    await db.execute(
        """
        UPDATE results
        SET status = 'pending', source = 'none', reason = NULL, updated_at = ?
        WHERE status IN ('running', 'queued_browser')
        """,
        (utc_now(),),
    )
    await db.execute(
        """
        UPDATE jobs
        SET status = 'queued', started_at = NULL, completed_at = NULL,
            notes = 'Recovered after restart'
        WHERE status = 'running'
        """
    )


async def get_pending_results(db: Database, job_id: str) -> list[dict[str, Any]]:
    return await db.fetchall(
        "SELECT id, code FROM results WHERE job_id = ? AND status = 'pending' ORDER BY id ASC",
        (job_id,),
    )


async def mark_result_running(db: Database, result_id: int) -> None:
    await db.execute(
        "UPDATE results SET status = ?, source = 'none', updated_at = ? WHERE id = ?",
        ("running", utc_now(), result_id),
    )


async def mark_result_queued_browser(
    db: Database,
    result_id: int,
    *,
    reason: str,
    attempts: int,
    http_status: int | None,
    redirect_url: str | None,
) -> None:
    await db.execute(
        """
        UPDATE results
        SET status = ?, source = ?, reason = ?, attempts = ?, http_status = ?,
            redirect_url = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            "queued_browser",
            "http",
            reason[:500],
            attempts,
            http_status,
            redirect_url,
            utc_now(),
            result_id,
        ),
    )


async def mark_result_final(
    db: Database,
    result_id: int,
    *,
    status: str,
    source: str,
    reason: str,
    attempts: int,
    http_status: int | None,
    redirect_url: str | None,
) -> None:
    now = utc_now()
    await db.execute(
        """
        UPDATE results
        SET status = ?, source = ?, reason = ?, attempts = ?,
            http_status = ?, redirect_url = ?, checked_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            source,
            reason[:500],
            attempts,
            http_status,
            redirect_url,
            now,
            now,
            result_id,
        ),
    )


async def get_job_counts(db: Database, job_id: str) -> dict[str, Any]:
    job = await db.fetchone("SELECT total_codes FROM jobs WHERE id = ?", (job_id,))
    if job is None:
        return {"total": 0, "processed": 0, "progress_percent": 0.0, "by_status": {}}

    total = int(job["total_codes"])
    rows = await db.fetchall(
        "SELECT status, COUNT(*) AS count FROM results WHERE job_id = ? GROUP BY status",
        (job_id,),
    )

    counts = {row["status"]: int(row["count"]) for row in rows}
    processed = sum(count for status, count in counts.items() if status in FINAL_RESULT_STATUSES)
    progress_percent = 0.0 if total == 0 else round((processed / total) * 100.0, 2)

    return {
        "total": total,
        "processed": processed,
        "progress_percent": progress_percent,
        "by_status": counts,
    }


async def list_results(
    db: Database,
    job_id: str,
    *,
    limit: int,
    offset: int,
    status: str | None,
) -> list[dict[str, Any]]:
    params: list[Any] = [job_id]
    where_clause = "WHERE job_id = ?"
    if status:
        where_clause += " AND status = ?"
        params.append(status)

    params.extend([limit, offset])
    return await db.fetchall(
        f"""
        SELECT id, code, status, source, reason, attempts, http_status, redirect_url, checked_at, updated_at
        FROM results
        {where_clause}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )


async def list_results_for_export(db: Database, job_id: str) -> list[dict[str, Any]]:
    return await db.fetchall(
        """
        SELECT code, status, source, reason, attempts, http_status, redirect_url, checked_at
        FROM results
        WHERE job_id = ?
        ORDER BY id ASC
        """,
        (job_id,),
    )


async def rerun_uncertain_results(db: Database, job_id: str) -> int:
    if db.conn is None:
        raise RuntimeError("Database is not connected")

    now = utc_now()
    cursor = await db.conn.execute(
        """
        UPDATE results
        SET status = 'pending', source = 'none', reason = NULL, attempts = 0,
            http_status = NULL, redirect_url = NULL, checked_at = NULL, updated_at = ?
        WHERE job_id = ? AND status IN ('unknown', 'blocked', 'error')
        """,
        (now, job_id),
    )
    changed = cursor.rowcount if cursor.rowcount is not None else 0

    await db.conn.execute(
        "UPDATE jobs SET status = 'queued', started_at = NULL, completed_at = NULL, notes = NULL WHERE id = ?",
        (job_id,),
    )
    await db.conn.commit()
    return changed
