# -*- coding: utf-8 -*-
"""SQLite 不可变版本存储。

版本按输入内容寻址：同一份规划输入永远得到同一个版本；
版本一经写入，其输入与结果不再改变。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import PlanRequest, PlanResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    request_json  TEXT NOT NULL,
    result_json   TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonicalize_request(req: PlanRequest) -> tuple[str, PlanRequest]:
    """生成规范化输入：无序字段排序、显式补全掌握状态。

    返回 (canonical_json, 规范化后的请求)。先修方案的顺序是有语义的
    （决定命中哪个替代方案），因此保留。
    """
    concepts = sorted(req.concepts, key=lambda c: c.id)
    materials = sorted(req.materials, key=lambda m: m.id)

    mastery: dict[str, str] = {}
    for c in concepts:
        mastery[c.id] = req.mastery.get(c.id, "not_mastered")

    normalized = PlanRequest(
        concepts=concepts,
        materials=[
            m.model_copy(update={"teaches": sorted(set(m.teaches))})
            for m in materials
        ],
        goal=req.goal,
        mastery=mastery,
    )
    raw = normalized.model_dump(mode="json")
    canonical = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return canonical, normalized


def version_id(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class VersionStore:
    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        path = Path(
            db_path
            or os.environ.get("PAPERCARDS_DB")
            or "data/papercards.db"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with self.conn:
            self.conn.executescript(_SCHEMA)

    # ---- 写入 ----

    def create_or_get(
        self, canonical: str, normalized: PlanRequest, result: PlanResult
    ) -> tuple[str, str, bool]:
        """内容寻址写入。返回 (version_id, created_at, created_now)。"""
        vid = version_id(canonical)
        row = self.conn.execute(
            "SELECT id, created_at FROM versions WHERE id = ?", (vid,)
        ).fetchone()
        if row is not None:
            # 输入与结果不可变；仅复用，不覆盖
            with self.conn:
                self.conn.execute(
                    "UPDATE versions SET request_count = request_count + 1 WHERE id = ?",
                    (vid,),
                )
            return vid, row["created_at"], False

        created_at = _now()
        with self.conn:
            self.conn.execute(
                "INSERT INTO versions (id, created_at, request_json, result_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    vid,
                    created_at,
                    canonical,
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                ),
            )
        return vid, created_at, True

    # ---- 读取（直接读存档，保证旧版本不漂移）----

    def get(self, vid: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, created_at, request_json, result_json FROM versions WHERE id = ?",
            (vid,),
        ).fetchone()
        if row is None:
            return None
        return {
            "version": {
                "id": row["id"],
                "created_at": row["created_at"],
                "goal": json.loads(row["request_json"])["goal"],
                "result_status": json.loads(row["result_json"])["status"],
            },
            "request": json.loads(row["request_json"]),
            "result": json.loads(row["result_json"]),
        }

    def list_versions(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, created_at, request_json, result_json "
            "FROM versions ORDER BY created_at DESC, id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "goal": json.loads(r["request_json"])["goal"],
                "result_status": json.loads(r["result_json"])["status"],
            }
            for r in rows
        ]
