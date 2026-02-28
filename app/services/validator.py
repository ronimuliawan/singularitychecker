from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx


RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class ValidationOutcome:
    status: str
    source: str
    reason: str
    attempts: int = 1
    http_status: int | None = None
    redirect_url: str | None = None


def _normalize_text(value: str) -> str:
    return value.lower().strip()


def _contains_any(text: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    normalized = _normalize_text(text)
    for pattern in patterns:
        token = _normalize_text(pattern)
        if token and token in normalized:
            return True
    return False


def _rule_matches(rule: dict[str, Any], status_code: int, body_text: str, final_url: str) -> bool:
    status_codes = rule.get("status_codes") or []
    if status_codes and status_code in status_codes:
        return True
    if _contains_any(body_text, rule.get("body_contains_any") or []):
        return True
    if _contains_any(final_url, rule.get("url_contains_any") or []):
        return True
    return False


def render_code_url(template: str, code: str) -> str:
    escaped = quote(code, safe="")
    if "{code}" in template:
        return template.replace("{code}", escaped)
    separator = "&" if "?" in template else "?"
    return f"{template}{separator}code={escaped}"


def build_http_request(
    profile: dict[str, Any],
    code: str,
    redeem_url_override: str | None,
) -> dict[str, Any] | None:
    http_cfg = profile["http"]
    if not http_cfg.get("enabled", True):
        return None

    method = str(http_cfg.get("method", "GET")).upper()
    mode = profile.get("mode")

    if mode == "url_template":
        template = (redeem_url_override or profile.get("url_template") or "").strip()
        if not template:
            return None
        return {
            "method": method,
            "url": render_code_url(template, code),
            "params": None,
            "data": None,
        }

    post_url = (http_cfg.get("post_url") or profile.get("form", {}).get("url") or "").strip()
    if not post_url:
        return None

    code_field = str(http_cfg.get("code_field") or "code")
    if method == "GET":
        return {
            "method": method,
            "url": post_url,
            "params": {code_field: code},
            "data": None,
        }

    return {
        "method": method,
        "url": post_url,
        "params": None,
        "data": {code_field: code},
    }


def classify_http_response(response: httpx.Response, profile: dict[str, Any]) -> tuple[str, str]:
    text = response.text[:200000]
    final_url = str(response.url)
    status_code = response.status_code
    http_cfg = profile["http"]

    blocked_rule = http_cfg.get("blocked", {})
    if _rule_matches(blocked_rule, status_code, text, final_url):
        return "blocked", "Matched blocked rule"

    success_rule = http_cfg.get("success", {})
    failure_rule = http_cfg.get("failure", {})
    success_match = _rule_matches(success_rule, status_code, text, final_url)
    failure_match = _rule_matches(failure_rule, status_code, text, final_url)

    if success_match and not failure_match:
        return "valid", "Matched success rule"
    if failure_match and not success_match:
        return "invalid", "Matched failure rule"
    if success_match and failure_match:
        return "unknown", "Conflicting success and failure signals"

    return "unknown", "No configured HTTP rule matched"


def classify_browser_content(content: str, current_url: str, profile: dict[str, Any]) -> tuple[str, str]:
    browser_cfg = profile["browser"]
    http_cfg = profile["http"]

    blocked_patterns = list(browser_cfg.get("blocked_text_any") or []) + list(
        (http_cfg.get("blocked") or {}).get("body_contains_any") or []
    )
    if _contains_any(content, blocked_patterns):
        return "blocked", "Blocked text detected in browser content"

    success_patterns = list(browser_cfg.get("success_text_any") or []) + list(
        (http_cfg.get("success") or {}).get("body_contains_any") or []
    )
    failure_patterns = list(browser_cfg.get("failure_text_any") or []) + list(
        (http_cfg.get("failure") or {}).get("body_contains_any") or []
    )

    success_match = _contains_any(content, success_patterns)
    failure_match = _contains_any(content, failure_patterns)

    if success_match and not failure_match:
        return "valid", "Browser content matched success text"
    if failure_match and not success_match:
        return "invalid", "Browser content matched failure text"
    if success_match and failure_match:
        return "unknown", "Browser found both success and failure text"

    if _contains_any(current_url, (http_cfg.get("success") or {}).get("url_contains_any") or []):
        return "valid", "Current URL matched success rule"
    if _contains_any(current_url, (http_cfg.get("failure") or {}).get("url_contains_any") or []):
        return "invalid", "Current URL matched failure rule"

    return "unknown", "No configured browser rule matched"


def _is_retryable_http_result(outcome: ValidationOutcome) -> bool:
    if outcome.status == "error":
        return True
    if outcome.http_status in RETRYABLE_HTTP_STATUSES:
        return True
    if outcome.status == "blocked" and outcome.http_status in {403, 429, 503}:
        if "captcha" in outcome.reason.lower():
            return False
        return True
    return False


async def run_http_validation(
    client: httpx.AsyncClient,
    profile: dict[str, Any],
    code: str,
    redeem_url_override: str | None,
    max_retries: int,
    request_delay_ms: int,
) -> ValidationOutcome:
    request_data = build_http_request(profile, code, redeem_url_override)
    if request_data is None:
        return ValidationOutcome(
            status="unknown",
            source="http",
            reason="HTTP stage skipped by profile configuration",
            attempts=0,
        )

    total_attempts = max(1, max_retries + 1)
    last_outcome = ValidationOutcome(
        status="unknown",
        source="http",
        reason="HTTP stage did not execute",
        attempts=0,
    )

    for attempt in range(1, total_attempts + 1):
        if request_delay_ms > 0:
            jitter = random.uniform(0.8, 1.2)
            await asyncio.sleep((request_delay_ms * jitter) / 1000.0)

        try:
            response = await client.request(
                request_data["method"],
                request_data["url"],
                params=request_data.get("params"),
                data=request_data.get("data"),
            )
            status, reason = classify_http_response(response, profile)
            last_outcome = ValidationOutcome(
                status=status,
                source="http",
                reason=reason,
                attempts=attempt,
                http_status=response.status_code,
                redirect_url=str(response.url),
            )
        except Exception as exc:
            last_outcome = ValidationOutcome(
                status="error",
                source="http",
                reason=f"HTTP exception: {exc.__class__.__name__}",
                attempts=attempt,
            )

        if attempt < total_attempts and _is_retryable_http_result(last_outcome):
            await asyncio.sleep(min(8.0, 0.75 * attempt + random.random()))
            continue

        return last_outcome

    return last_outcome


def needs_browser_fallback(outcome: ValidationOutcome, profile: dict[str, Any]) -> bool:
    if not profile["browser"].get("enabled", True):
        return False
    return outcome.status in {"unknown", "blocked", "error"}


async def run_browser_validation(
    browser_context: Any,
    profile: dict[str, Any],
    code: str,
    redeem_url_override: str | None,
) -> ValidationOutcome:
    page = await browser_context.new_page()
    timeout_ms = int(profile["browser"].get("timeout_ms", 45000))
    wait_after_submit_ms = int(profile["browser"].get("wait_after_submit_ms", 2000))

    try:
        mode = profile.get("mode")
        if mode == "url_template":
            template = (redeem_url_override or profile.get("url_template") or "").strip()
            if not template:
                return ValidationOutcome(
                    status="unknown",
                    source="browser",
                    reason="Browser stage missing URL template",
                    attempts=1,
                )
            target_url = render_code_url(template, code)
            await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
        else:
            form_cfg = profile.get("form", {})
            target_url = (form_cfg.get("url") or "").strip()
            code_selector = (form_cfg.get("code_selector") or "").strip()
            submit_selector = (form_cfg.get("submit_selector") or "").strip()

            if not target_url or not code_selector or not submit_selector:
                return ValidationOutcome(
                    status="unknown",
                    source="browser",
                    reason="Browser stage missing form settings",
                    attempts=1,
                )

            await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.fill(code_selector, code, timeout=timeout_ms)
            await page.click(submit_selector, timeout=timeout_ms)

        wait_selector = (profile.get("form", {}).get("wait_for_selector") or "").strip()
        result_selector = (profile["browser"].get("result_selector") or "").strip()
        active_wait_selector = wait_selector or result_selector

        if active_wait_selector:
            try:
                await page.wait_for_selector(active_wait_selector, timeout=timeout_ms)
            except Exception:
                pass

        if wait_after_submit_ms > 0:
            await page.wait_for_timeout(wait_after_submit_ms)

        result_text = ""
        if result_selector:
            try:
                result_text = await page.inner_text(result_selector, timeout=1500)
            except Exception:
                result_text = ""

        content = await page.content()
        combined = f"{result_text}\n{content}"
        status, reason = classify_browser_content(combined, page.url, profile)
        return ValidationOutcome(
            status=status,
            source="browser",
            reason=reason,
            attempts=1,
            redirect_url=page.url,
        )
    except Exception as exc:
        return ValidationOutcome(
            status="error",
            source="browser",
            reason=f"Browser exception: {exc.__class__.__name__}",
            attempts=1,
        )
    finally:
        await page.close()


def load_http_cookies_from_storage_state(storage_state_path: Path) -> httpx.Cookies:
    cookies = httpx.Cookies()
    if not storage_state_path.exists():
        return cookies

    try:
        payload = json.loads(storage_state_path.read_text(encoding="utf-8"))
    except Exception:
        return cookies

    cookie_entries = payload.get("cookies") if isinstance(payload, dict) else None
    if not isinstance(cookie_entries, list):
        return cookies

    for entry in cookie_entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        value = str(entry.get("value", "")).strip()
        domain = str(entry.get("domain", "")).strip() or None
        path = str(entry.get("path", "/")).strip() or "/"
        if not name:
            continue
        if domain:
            cookies.set(name=name, value=value, domain=domain, path=path)
        else:
            cookies.set(name=name, value=value, path=path)

    return cookies
