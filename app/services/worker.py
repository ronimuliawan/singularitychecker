from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app import repository
from app.services import validator
from app.services.validator import ValidationOutcome


class JobManager:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self._shutdown = False

    async def start_queued_jobs(self) -> None:
        queued = await repository.list_jobs_by_status(self.app.state.db, ("queued",))
        for job in queued:
            self.start_job(job["id"])

    def start_job(self, job_id: str) -> None:
        if self._shutdown:
            return
        existing = self.tasks.get(job_id)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(self._run_job(job_id), name=f"job-{job_id}")
        self.tasks[job_id] = task

    async def shutdown(self) -> None:
        self._shutdown = True
        running = [task for task in self.tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    async def _run_job(self, job_id: str) -> None:
        db = self.app.state.db
        profile_store = self.app.state.profile_store

        job = await repository.get_job(db, job_id)
        if job is None:
            return

        profile = profile_store.get(job["profile_name"])
        if profile is None:
            await repository.mark_job_failed(db, job_id, f"Profile not found: {job['profile_name']}")
            return

        try:
            await repository.mark_job_running(db, job_id)
            pending_results = await repository.get_pending_results(db, job_id)

            if not pending_results:
                await repository.mark_job_completed(db, job_id)
                return

            http_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            browser_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            for row in pending_results:
                await http_queue.put(row)

            http_workers = [
                asyncio.create_task(
                    self._http_worker(job, profile, http_queue, browser_queue),
                    name=f"http-worker-{job_id}-{index}",
                )
                for index in range(max(1, int(job["http_concurrency"])))
            ]

            browser_enabled = bool(profile["browser"].get("enabled", True)) and int(
                job["browser_concurrency"]
            ) > 0
            browser_workers: list[asyncio.Task[None]] = []
            if browser_enabled:
                browser_workers = [
                    asyncio.create_task(
                        self._browser_worker(job, profile, browser_queue),
                        name=f"browser-worker-{job_id}-{index}",
                    )
                    for index in range(max(1, int(job["browser_concurrency"])))
                ]

            await http_queue.join()
            for _ in http_workers:
                await http_queue.put(None)
            await asyncio.gather(*http_workers, return_exceptions=True)

            if browser_workers:
                await browser_queue.join()
                for _ in browser_workers:
                    await browser_queue.put(None)
                await asyncio.gather(*browser_workers, return_exceptions=True)

            await repository.mark_job_completed(db, job_id)
        except Exception as exc:
            await repository.mark_job_failed(db, job_id, f"Job failed: {exc.__class__.__name__}")

    async def _http_worker(
        self,
        job: dict[str, Any],
        profile: dict[str, Any],
        http_queue: asyncio.Queue[dict[str, Any] | None],
        browser_queue: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        db = self.app.state.db
        profile_store = self.app.state.profile_store

        storage_state_path = profile_store.resolve_storage_state_path(profile["name"])
        cookies = validator.load_http_cookies_from_storage_state(storage_state_path)
        timeout = httpx.Timeout(float(profile["http"].get("timeout_seconds", 20)))

        headers = dict(profile["http"].get("headers") or {})
        if not any(key.lower() == "user-agent" for key in headers):
            headers["User-Agent"] = "Mozilla/5.0 RedeemChecker/1.0"

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
            cookies=cookies,
        ) as client:
            while True:
                item = await http_queue.get()
                if item is None:
                    http_queue.task_done()
                    break

                result_id = int(item["id"])
                code = str(item["code"])

                try:
                    await repository.mark_result_running(db, result_id)
                    outcome = await validator.run_http_validation(
                        client,
                        profile,
                        code,
                        job.get("redeem_url_override"),
                        int(job["max_retries"]),
                        int(job["request_delay_ms"]),
                    )

                    if validator.needs_browser_fallback(outcome, profile) and int(
                        job["browser_concurrency"]
                    ) > 0:
                        await repository.mark_result_queued_browser(
                            db,
                            result_id,
                            reason=outcome.reason,
                            attempts=outcome.attempts,
                            http_status=outcome.http_status,
                            redirect_url=outcome.redirect_url,
                        )
                        await browser_queue.put(
                            {
                                "result_id": result_id,
                                "code": code,
                                "http_outcome": {
                                    "status": outcome.status,
                                    "source": outcome.source,
                                    "reason": outcome.reason,
                                    "attempts": outcome.attempts,
                                    "http_status": outcome.http_status,
                                    "redirect_url": outcome.redirect_url,
                                },
                            }
                        )
                    else:
                        await repository.mark_result_final(
                            db,
                            result_id,
                            status=outcome.status,
                            source=outcome.source,
                            reason=outcome.reason,
                            attempts=outcome.attempts,
                            http_status=outcome.http_status,
                            redirect_url=outcome.redirect_url,
                        )
                except Exception as exc:
                    await repository.mark_result_final(
                        db,
                        result_id,
                        status="error",
                        source="http",
                        reason=f"HTTP worker exception: {exc.__class__.__name__}",
                        attempts=1,
                        http_status=None,
                        redirect_url=None,
                    )
                finally:
                    http_queue.task_done()

    async def _browser_worker(
        self,
        job: dict[str, Any],
        profile: dict[str, Any],
        browser_queue: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        db = self.app.state.db
        profile_store = self.app.state.profile_store

        browser_ready = False
        startup_error = ""
        playwright_runtime = None
        browser = None
        context = None

        try:
            from playwright.async_api import async_playwright

            playwright_runtime = await async_playwright().start()
            browser = await playwright_runtime.chromium.launch(
                headless=bool(profile["browser"].get("headless", True))
            )

            context_kwargs: dict[str, Any] = {}
            storage_state_path = profile_store.resolve_storage_state_path(profile["name"])
            if storage_state_path.exists():
                context_kwargs["storage_state"] = str(storage_state_path)

            context = await browser.new_context(**context_kwargs)
            browser_ready = True
        except Exception as exc:
            startup_error = f"Browser startup failed: {exc.__class__.__name__}"

        try:
            while True:
                item = await browser_queue.get()
                if item is None:
                    browser_queue.task_done()
                    break

                result_id = int(item["result_id"])
                code = str(item["code"])
                prior = ValidationOutcome(**item["http_outcome"])

                try:
                    if browser_ready and context is not None:
                        browser_outcome = await validator.run_browser_validation(
                            context,
                            profile,
                            code,
                            job.get("redeem_url_override"),
                        )
                        final_outcome = ValidationOutcome(
                            status=browser_outcome.status,
                            source=browser_outcome.source,
                            reason=browser_outcome.reason,
                            attempts=max(0, prior.attempts) + max(1, browser_outcome.attempts),
                            http_status=prior.http_status,
                            redirect_url=browser_outcome.redirect_url or prior.redirect_url,
                        )
                    else:
                        final_outcome = ValidationOutcome(
                            status=prior.status,
                            source=prior.source,
                            reason=f"{prior.reason}; {startup_error}",
                            attempts=prior.attempts,
                            http_status=prior.http_status,
                            redirect_url=prior.redirect_url,
                        )

                    await repository.mark_result_final(
                        db,
                        result_id,
                        status=final_outcome.status,
                        source=final_outcome.source,
                        reason=final_outcome.reason,
                        attempts=final_outcome.attempts,
                        http_status=final_outcome.http_status,
                        redirect_url=final_outcome.redirect_url,
                    )
                except Exception as exc:
                    await repository.mark_result_final(
                        db,
                        result_id,
                        status="error",
                        source="browser",
                        reason=f"Browser worker exception: {exc.__class__.__name__}",
                        attempts=max(1, prior.attempts),
                        http_status=prior.http_status,
                        redirect_url=prior.redirect_url,
                    )
                finally:
                    browser_queue.task_done()
        finally:
            if context is not None:
                await context.close()
            if browser is not None:
                await browser.close()
            if playwright_runtime is not None:
                await playwright_runtime.stop()
