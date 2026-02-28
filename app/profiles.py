from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULT_BLOCKED_KEYWORDS = [
    "captcha",
    "verify you are human",
    "are you human",
    "attention required",
    "cloudflare",
    "security check",
    "access denied",
]


class ProfileStore:
    def __init__(self, profiles_dir: Path, base_dir: Path) -> None:
        self.profiles_dir = profiles_dir
        self.base_dir = base_dir
        self._profiles: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
        self._profiles.clear()
        files = sorted(list(self.profiles_dir.glob("*.yaml")) + list(self.profiles_dir.glob("*.yml")))
        for profile_file in files:
            try:
                raw = yaml.safe_load(profile_file.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue

            profile = self._normalize_profile(raw, profile_file)
            self._profiles[profile["name"]] = profile

    def names(self) -> list[str]:
        return sorted(self._profiles.keys())

    def get(self, profile_name: str) -> dict[str, Any] | None:
        return self._profiles.get(profile_name)

    def all_public(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for name in self.names():
            profile = self._profiles[name]
            session_state = self.resolve_storage_state_path(name)
            output.append(
                {
                    "name": profile["name"],
                    "description": profile.get("description", ""),
                    "mode": profile["mode"],
                    "login_required": bool(profile["browser"].get("login_required", False)),
                    "has_session_state": session_state.exists(),
                }
            )
        return output

    def resolve_storage_state_path(self, profile_name: str) -> Path:
        profile = self._profiles[profile_name]
        raw_path = profile["browser"].get("storage_state_path", f"sessions/{profile_name}.json")
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return (self.base_dir / path).resolve()

    def _normalize_profile(self, raw: dict[str, Any], profile_file: Path) -> dict[str, Any]:
        name = str(raw.get("name") or profile_file.stem).strip()
        if not name:
            name = profile_file.stem

        mode = str(raw.get("mode", "url_template")).strip().lower()
        if mode not in {"url_template", "form"}:
            mode = "url_template"

        http_cfg = raw.get("http") or {}
        browser_cfg = raw.get("browser") or {}
        form_cfg = raw.get("form") or {}

        blocked_http = self._normalize_rule(http_cfg.get("blocked"))
        blocked_http["body_contains_any"] = self._dedupe_strings(
            blocked_http.get("body_contains_any", []) + DEFAULT_BLOCKED_KEYWORDS
        )
        blocked_browser = self._string_list(browser_cfg.get("blocked_text_any"))
        blocked_browser = self._dedupe_strings(blocked_browser + DEFAULT_BLOCKED_KEYWORDS)

        profile: dict[str, Any] = {
            "name": name,
            "description": str(raw.get("description", "")).strip(),
            "mode": mode,
            "url_template": str(raw.get("url_template", "")).strip(),
            "form": {
                "url": str(form_cfg.get("url", "")).strip(),
                "code_selector": str(form_cfg.get("code_selector", "")).strip(),
                "submit_selector": str(form_cfg.get("submit_selector", "")).strip(),
                "wait_for_selector": str(form_cfg.get("wait_for_selector", "")).strip(),
            },
            "http": {
                "enabled": bool(http_cfg.get("enabled", True)),
                "method": str(http_cfg.get("method", "GET")).upper(),
                "timeout_seconds": self._int_or_default(http_cfg.get("timeout_seconds"), 20, 1),
                "headers": self._dict_or_empty(http_cfg.get("headers")),
                "post_url": str(http_cfg.get("post_url", "")).strip(),
                "code_field": str(http_cfg.get("code_field", "code")).strip(),
                "success": self._normalize_rule(http_cfg.get("success")),
                "failure": self._normalize_rule(http_cfg.get("failure")),
                "blocked": blocked_http,
            },
            "browser": {
                "enabled": bool(browser_cfg.get("enabled", True)),
                "headless": bool(browser_cfg.get("headless", True)),
                "login_required": bool(browser_cfg.get("login_required", False)),
                "timeout_ms": self._int_or_default(browser_cfg.get("timeout_ms"), 45000, 1000),
                "wait_after_submit_ms": self._int_or_default(
                    browser_cfg.get("wait_after_submit_ms"),
                    2000,
                    0,
                ),
                "result_selector": str(browser_cfg.get("result_selector", "")).strip(),
                "storage_state_path": str(
                    browser_cfg.get("storage_state_path", f"sessions/{name}.json")
                ).strip(),
                "success_text_any": self._string_list(browser_cfg.get("success_text_any")),
                "failure_text_any": self._string_list(browser_cfg.get("failure_text_any")),
                "blocked_text_any": blocked_browser,
            },
        }

        if not profile["url_template"] and mode == "url_template":
            profile["url_template"] = "https://example.com/redeem?code={code}"

        return profile

    @staticmethod
    def _int_or_default(value: Any, default: int, minimum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, parsed)

    @staticmethod
    def _dict_or_empty(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        cleaned: dict[str, str] = {}
        for key, item in value.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            cleaned[key_text] = str(item)
        return cleaned

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _normalize_rule(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            value = {}

        status_codes = value.get("status_codes")
        code_values: list[int] = []
        if isinstance(status_codes, list):
            for item in status_codes:
                try:
                    code_values.append(int(item))
                except (TypeError, ValueError):
                    continue

        return {
            "status_codes": code_values,
            "body_contains_any": ProfileStore._string_list(value.get("body_contains_any")),
            "url_contains_any": ProfileStore._string_list(value.get("url_contains_any")),
        }

    @staticmethod
    def _dedupe_strings(items: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            normalized = item.lower().strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            output.append(item)
        return output
