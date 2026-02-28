from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from fastapi import UploadFile


SPLIT_PATTERN = re.compile(r"[\s,;|]+")


@dataclass
class CodeCollection:
    codes: list[str]
    raw_count: int
    unique_count: int

    @property
    def duplicates_removed(self) -> int:
        return max(0, self.raw_count - self.unique_count)


def _normalize_code(token: str) -> str:
    return token.strip().strip('"').strip("'")


def parse_codes_from_text(text: str) -> list[str]:
    if not text:
        return []
    raw_tokens = SPLIT_PATTERN.split(text)
    cleaned: list[str] = []
    for token in raw_tokens:
        normalized = _normalize_code(token)
        if normalized:
            cleaned.append(normalized)
    return cleaned


def parse_codes_from_csv_text(text: str) -> list[str]:
    if not text:
        return []

    reader = csv.reader(io.StringIO(text))
    tokens: list[str] = []
    for row in reader:
        for cell in row:
            normalized = _normalize_code(cell)
            if normalized:
                tokens.append(normalized)
    return tokens


async def parse_codes_from_upload(upload: UploadFile) -> list[str]:
    blob = await upload.read()
    text = blob.decode("utf-8", errors="ignore")
    filename = (upload.filename or "").lower()

    if filename.endswith(".csv"):
        return parse_codes_from_csv_text(text)
    return parse_codes_from_text(text)


async def collect_codes(pasted_codes: str, files: list[UploadFile]) -> CodeCollection:
    merged: list[str] = []
    if pasted_codes.strip():
        merged.extend(parse_codes_from_text(pasted_codes))

    for upload in files:
        merged.extend(await parse_codes_from_upload(upload))

    seen: set[str] = set()
    deduped: list[str] = []
    for code in merged:
        if code in seen:
            continue
        seen.add(code)
        deduped.append(code)

    return CodeCollection(codes=deduped, raw_count=len(merged), unique_count=len(deduped))
