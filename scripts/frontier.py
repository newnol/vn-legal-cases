from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from common import extract_source_case_id


STATUS_DISCOVERED = "discovered"
STATUS_FETCHING = "fetching"
STATUS_FETCHED = "fetched"
STATUS_FAILED = "failed"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class FrontierItem:
    detail_url: str
    source_case_id: str
    status: str
    priority: int
    attempts: int
    discovery_source: str
    first_seen_at: str
    last_seen_at: str
    last_attempt_at: str | None
    next_eligible_at: str | None
    last_error: str | None


class FrontierStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.db_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS frontier (
                detail_url TEXT PRIMARY KEY,
                source_case_id TEXT NOT NULL,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 100,
                attempts INTEGER NOT NULL DEFAULT 0,
                discovery_source TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_attempt_at TEXT,
                next_eligible_at TEXT,
                last_error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_frontier_status_priority
                ON frontier(status, priority, last_seen_at);

            CREATE INDEX IF NOT EXISTS idx_frontier_source_case_id
                ON frontier(source_case_id);
            """
        )
        self.connection.commit()

    def upsert_urls(self, urls: Iterable[str], *, discovery_source: str, priority: int = 100) -> int:
        now = utc_now_iso()
        inserted = 0
        for url in urls:
            source_case_id = extract_source_case_id(url)
            cursor = self.connection.execute(
                """
                INSERT INTO frontier (
                    detail_url,
                    source_case_id,
                    status,
                    priority,
                    attempts,
                    discovery_source,
                    first_seen_at,
                    last_seen_at,
                    last_attempt_at,
                    next_eligible_at,
                    last_error
                )
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, NULL, NULL, NULL)
                ON CONFLICT(detail_url) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    priority=MIN(frontier.priority, excluded.priority),
                    discovery_source=CASE
                        WHEN frontier.discovery_source = excluded.discovery_source
                        THEN frontier.discovery_source
                        ELSE frontier.discovery_source || ',' || excluded.discovery_source
                    END
                """,
                (
                    url,
                    source_case_id,
                    STATUS_DISCOVERED,
                    priority,
                    discovery_source,
                    now,
                    now,
                ),
            )
            inserted += 1 if cursor.rowcount == 1 else 0

        self.connection.commit()
        return inserted

    def claim_batch(self, limit: int) -> list[FrontierItem]:
        now = utc_now_iso()
        rows = self.connection.execute(
            """
            SELECT detail_url
            FROM frontier
            WHERE status IN (?, ?)
              AND (next_eligible_at IS NULL OR next_eligible_at <= ?)
            ORDER BY priority ASC, last_seen_at ASC
            LIMIT ?
            """,
            (STATUS_DISCOVERED, STATUS_FAILED, now, limit),
        ).fetchall()

        claimed_urls = [row["detail_url"] for row in rows]
        if not claimed_urls:
            return []

        placeholders = ",".join("?" for _ in claimed_urls)
        self.connection.execute(
            f"""
            UPDATE frontier
            SET status = ?, last_attempt_at = ?
            WHERE detail_url IN ({placeholders})
            """,
            (STATUS_FETCHING, now, *claimed_urls),
        )
        self.connection.commit()
        return self.get_items(claimed_urls)

    def mark_fetched(self, detail_url: str) -> None:
        self.connection.execute(
            """
            UPDATE frontier
            SET status = ?, next_eligible_at = NULL, last_error = NULL
            WHERE detail_url = ?
            """,
            (STATUS_FETCHED, detail_url),
        )
        self.connection.commit()

    def mark_failed(self, detail_url: str, error: str, *, retry_delay_seconds: int) -> None:
        row = self.connection.execute(
            "SELECT attempts FROM frontier WHERE detail_url = ?",
            (detail_url,),
        ).fetchone()
        attempts = int(row["attempts"]) + 1 if row else 1
        self.connection.execute(
            """
            UPDATE frontier
            SET status = ?,
                attempts = ?,
                last_error = ?,
                next_eligible_at = ?
            WHERE detail_url = ?
            """,
            (STATUS_FAILED, attempts, error[:1000], utc_after(retry_delay_seconds), detail_url),
        )
        self.connection.commit()

    def reset_fetching(self) -> int:
        cursor = self.connection.execute(
            """
            UPDATE frontier
            SET status = ?, next_eligible_at = NULL
            WHERE status = ?
            """,
            (STATUS_DISCOVERED, STATUS_FETCHING),
        )
        self.connection.commit()
        return cursor.rowcount

    def reclaim_stale_fetching(self, *, stale_after_seconds: int) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        ).replace(microsecond=0).isoformat()
        cursor = self.connection.execute(
            """
            UPDATE frontier
            SET status = ?,
                next_eligible_at = NULL,
                last_error = CASE
                    WHEN last_error IS NULL OR last_error = ''
                    THEN 'Recovered from interrupted run.'
                    ELSE last_error
                END
            WHERE status = ?
              AND (last_attempt_at IS NULL OR last_attempt_at <= ?)
            """,
            (STATUS_DISCOVERED, STATUS_FETCHING, cutoff),
        )
        self.connection.commit()
        return cursor.rowcount

    def get_items(self, urls: Iterable[str]) -> list[FrontierItem]:
        url_list = list(urls)
        if not url_list:
            return []
        placeholders = ",".join("?" for _ in url_list)
        rows = self.connection.execute(
            f"""
            SELECT detail_url,
                   source_case_id,
                   status,
                   priority,
                   attempts,
                   discovery_source,
                   first_seen_at,
                   last_seen_at,
                   last_attempt_at,
                   next_eligible_at,
                   last_error
            FROM frontier
            WHERE detail_url IN ({placeholders})
            """,
            url_list,
        ).fetchall()
        return [FrontierItem(**dict(row)) for row in rows]

    def counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            """
            SELECT status, COUNT(*) AS total
            FROM frontier
            GROUP BY status
            """
        ).fetchall()
        counts = {row["status"]: int(row["total"]) for row in rows}
        counts["total"] = sum(counts.values())
        return counts
