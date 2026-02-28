from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite


class Database:
    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON;")
        await self.conn.execute("PRAGMA journal_mode = WAL;")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        if self.conn is None:
            raise RuntimeError("Database is not connected")
        await self.conn.execute(query, params)
        await self.conn.commit()

    async def executemany(self, query: str, params: list[tuple[Any, ...]]) -> None:
        if self.conn is None:
            raise RuntimeError("Database is not connected")
        await self.conn.executemany(query, params)
        await self.conn.commit()

    async def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if self.conn is None:
            raise RuntimeError("Database is not connected")
        cursor = await self.conn.execute(query, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.conn is None:
            raise RuntimeError("Database is not connected")
        cursor = await self.conn.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
