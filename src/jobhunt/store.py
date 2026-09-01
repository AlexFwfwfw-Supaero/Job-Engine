from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from jobhunt.models import Briefing, Employer, Event, EventKind, Job, Stage

SCHEMA = """
CREATE TABLE IF NOT EXISTS employers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    country TEXT DEFAULT '',
    city TEXT DEFAULT '',
    ats TEXT DEFAULT 'manual',
    ats_endpoint TEXT DEFAULT '',
    careers_url TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    poll_enabled INTEGER DEFAULT 0,
    read_everything INTEGER DEFAULT 0,
    last_polled TEXT,
    last_manual_check TEXT,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    employer_id INTEGER NOT NULL REFERENCES employers(id),
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    city TEXT DEFAULT '',
    country TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    snapshot_path TEXT DEFAULT '',
    first_seen TEXT,
    last_seen TEXT,
    stage TEXT DEFAULT 'spotted',
    dismissed INTEGER DEFAULT 0,
    dismiss_reason TEXT DEFAULT '',
    role_fit REAL DEFAULT 0,
    comp_score REAL DEFAULT 0,
    qol_score REAL DEFAULT 0,
    total_score REAL DEFAULT 0,
    salary_stated REAL,
    level TEXT DEFAULT 'junior',
    language_flags TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    base_cv TEXT DEFAULT '',
    angle TEXT DEFAULT '',
    applied_on TEXT,
    notes TEXT DEFAULT '',
    priority INTEGER DEFAULT 0,
    link_status TEXT DEFAULT 'unknown',
    last_checked TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT DEFAULT ''
);

-- One row per whole-set briefing. Kept rather than regenerated per page load:
-- a briefing costs a model call, and the CLI and the browser must show the
-- same text. Old ones stay so you can see how the advice moved.
CREATE TABLE IF NOT EXISTS briefings (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    scope TEXT DEFAULT '',
    job_count INTEGER DEFAULT 0,
    text TEXT NOT NULL
);

-- Postings whose advert we have already gone and read on its own page.
--
-- Some boards list a title and nothing else — Safran's 3803 rows carry no
-- advert at all — so a title the matcher cannot decide has to be fetched
-- before it can be judged. That is one request per posting, and the answer
-- for a posting that was not relevant does not change. Without this table
-- every poll would re-read three thousand pages to reach the same verdict.
--
-- Rows are kept for postings that were rejected as much as for ones that were
-- stored: the rejected ones are precisely the cost being avoided.
CREATE TABLE IF NOT EXISTS screened (
    url TEXT PRIMARY KEY,
    employer_id INTEGER,
    screened_at TEXT
);

-- Small facts about the tracker itself rather than about any one job. So far
-- one key: last_search_at, the timestamp of the most recent poll sweep, which
-- is what "new since the last search" is measured against.
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, ts);
CREATE INDEX IF NOT EXISTS idx_jobs_stage ON jobs(stage, dismissed);
"""

# Fields that belong to the user's tracking decisions and must never be
# overwritten when a poller re-sees a posting it has already recorded.
_PRESERVED_ON_REUPSERT = (
    "stage", "dismissed", "dismiss_reason", "base_cv", "angle",
    "applied_on", "first_seen", "notes", "priority",
    "description", "llm_fit", "llm_json", "llm_checked", "rank_score",
)

# Columns added to `employers` after release. Same idempotent ALTER as the
# jobs table, kept separate because the two tables migrate independently.
_ADDED_EMPLOYER_COLUMNS = (
    ("read_everything", "INTEGER DEFAULT 0"),
)

# Columns added to `jobs` after the first release. Applied to existing
# databases by _migrate(); listed here so a fresh database and a migrated one
# converge.
_ADDED_COLUMNS = (
    ("notes", "TEXT DEFAULT ''"),
    ("priority", "INTEGER DEFAULT 0"),
    ("link_status", "TEXT DEFAULT 'unknown'"),
    ("last_checked", "TEXT"),
    ("description", "TEXT DEFAULT ''"),
    ("llm_fit", "REAL"),
    ("llm_json", "TEXT DEFAULT ''"),
    ("llm_checked", "TEXT"),
    ("rank_score", "REAL DEFAULT 0"),
)

# meta key: when the most recent poll sweep started. Jobs first seen at or
# after it are the ones that sweep turned up.
LAST_SEARCH_AT = "last_search_at"

VALID_LINK_STATUS = frozenset({"live", "dead", "unknown"})
MIN_PRIORITY, MAX_PRIORITY = 0, 5


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def initialize(self) -> None:
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add post-release columns to an existing jobs table.

        Idempotent: reads the current columns and adds only what is missing,
        so it is safe on every startup and on a fresh database.
        """
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        for column, ddl in _ADDED_COLUMNS:
            if column not in existing:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {ddl}")

        existing_employer = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(employers)").fetchall()
        }
        for column, ddl in _ADDED_EMPLOYER_COLUMNS:
            if column not in existing_employer:
                self.conn.execute(
                    f"ALTER TABLE employers ADD COLUMN {column} {ddl}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- employers -----------------------------------------------------

    def upsert_employer(self, e: Employer) -> int:
        self.conn.execute(
            """
            INSERT INTO employers
                (name, country, city, ats, ats_endpoint, careers_url, tags,
                 poll_enabled, last_polled, last_manual_check, notes,
                 read_everything)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                country=excluded.country, city=excluded.city, ats=excluded.ats,
                ats_endpoint=excluded.ats_endpoint, careers_url=excluded.careers_url,
                tags=excluded.tags, poll_enabled=excluded.poll_enabled,
                notes=excluded.notes, read_everything=excluded.read_everything
            """,
            (e.name, e.country, e.city, e.ats, e.ats_endpoint, e.careers_url,
             json.dumps(e.tags), int(e.poll_enabled), e.last_polled,
             e.last_manual_check, e.notes, int(e.read_everything)),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM employers WHERE name = ?", (e.name,)
        ).fetchone()
        return int(row["id"])

    def get_employer(self, employer_id: int) -> Employer | None:
        row = self.conn.execute(
            "SELECT * FROM employers WHERE id = ?", (employer_id,)
        ).fetchone()
        return _row_to_employer(row) if row else None

    def find_employer_by_name(self, name: str) -> Employer | None:
        row = self.conn.execute(
            "SELECT * FROM employers WHERE name = ?", (name,)
        ).fetchone()
        return _row_to_employer(row) if row else None

    def list_employers(self) -> list[Employer]:
        rows = self.conn.execute("SELECT * FROM employers ORDER BY name").fetchall()
        return [_row_to_employer(r) for r in rows]

    # --- jobs ----------------------------------------------------------

    def upsert_job(self, j: Job) -> int:
        existing = self.conn.execute(
            "SELECT * FROM jobs WHERE url = ?", (j.url,)
        ).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE jobs SET
                    title=?, city=?, country=?, source=?, last_seen=?,
                    snapshot_path=COALESCE(NULLIF(?, ''), snapshot_path),
                    role_fit=?, comp_score=?, qol_score=?, total_score=?,
                    salary_stated=?, level=?, language_flags=?, tags=?,
                    rank_score=CASE
                        WHEN ? > 0 THEN ?
                        WHEN llm_fit IS NULL THEN ?
                        ELSE rank_score END
                WHERE id = ?
                """,
                (j.title, j.city, j.country, j.source, j.last_seen,
                 j.snapshot_path, j.role_fit, j.comp_score, j.qol_score,
                 j.total_score, j.salary_stated, j.level,
                 json.dumps(j.language_flags), json.dumps(j.tags),
                 j.rank_score, j.rank_score, j.total_score, existing["id"]),
            )
            self.conn.commit()
            return int(existing["id"])

        cur = self.conn.execute(
            """
            INSERT INTO jobs
                (employer_id, title, url, city, country, source, snapshot_path,
                 first_seen, last_seen, stage, dismissed, dismiss_reason,
                 role_fit, comp_score, qol_score, total_score, salary_stated,
                 level, language_flags, tags, base_cv, angle, applied_on,
                 description, rank_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?)
            """,
            (j.employer_id, j.title, j.url, j.city, j.country, j.source,
             j.snapshot_path, j.first_seen, j.last_seen, j.stage.value,
             int(j.dismissed), j.dismiss_reason, j.role_fit, j.comp_score,
             j.qol_score, j.total_score, j.salary_stated, j.level,
             json.dumps(j.language_flags), json.dumps(j.tags), j.base_cv,
             j.angle, j.applied_on, j.description,
             j.rank_score or j.total_score),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_job(self, job_id: int) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def list_jobs(
        self,
        *,
        stage: Stage | None = None,
        stages: list[Stage] | None = None,
        include_dismissed: bool = False,
        since: str | None = None,
    ) -> list[Job]:
        clauses, params = [], []
        if not include_dismissed:
            clauses.append("dismissed = 0")
        if stage is not None:
            clauses.append("stage = ?")
            params.append(stage.value)
        if stages:
            placeholders = ", ".join("?" for _ in stages)
            clauses.append(f"stage IN ({placeholders})")
            params.extend(s.value for s in stages)
        if since is not None:
            clauses.append("first_seen >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM jobs {where} "
            "ORDER BY priority DESC, rank_score DESC, total_score DESC, id DESC",
            params,
        ).fetchall()
        return [_row_to_job(r) for r in rows]

    def save_enrichment(
        self, job_id: int, description: str, llm_fit: float | None,
        llm_json: str, ts: str, rank_score: float | None = None,
    ) -> None:
        """Store the fetched posting text and the model's reading of it.

        Kept separate from upsert_job so re-polling never clears an analysis
        that cost an API call, and so a scoring change never rewrites it.
        """
        self.conn.execute(
            "UPDATE jobs SET description=?, llm_fit=?, llm_json=?, llm_checked=?, "
            "rank_score=COALESCE(?, rank_score) WHERE id = ?",
            (description, llm_fit, llm_json, ts, rank_score, job_id),
        )
        self.conn.commit()

    # --- briefings ------------------------------------------------------

    # --- meta ----------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else row["value"]

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def mark_screened(self, url: str, employer_id: int | None,
                      ts: str) -> None:
        """Record that this posting's own page has been read.

        Re-marking keeps the first date: what matters is that the request has
        already been paid for, not when.
        """
        self.conn.execute(
            "INSERT INTO screened (url, employer_id, screened_at) VALUES (?,?,?) "
            "ON CONFLICT(url) DO NOTHING",
            (url, employer_id, ts),
        )
        self.conn.commit()

    def screened_urls(self, employer_id: int | None = None) -> set[str]:
        if employer_id is None:
            rows = self.conn.execute("SELECT url FROM screened")
        else:
            rows = self.conn.execute(
                "SELECT url FROM screened WHERE employer_id = ?", (employer_id,)
            )
        return {row["url"] for row in rows}

    def save_briefing(self, ts: str, text: str, scope: str = "",
                      job_count: int = 0) -> int:
        cur = self.conn.execute(
            "INSERT INTO briefings (ts, scope, job_count, text) VALUES (?,?,?,?)",
            (ts, scope, job_count, text),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def latest_briefing(self) -> Briefing | None:
        row = self.conn.execute(
            "SELECT * FROM briefings ORDER BY ts DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return Briefing(id=row["id"], ts=row["ts"], scope=row["scope"],
                        job_count=row["job_count"], text=row["text"])

    def set_stage(self, job_id: int, stage: Stage, ts: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET stage = ? WHERE id = ?", (stage.value, job_id)
        )
        if stage is Stage.APPLIED:
            self.conn.execute(
                "UPDATE jobs SET applied_on = COALESCE(applied_on, ?) WHERE id = ?",
                (ts[:10], job_id),
            )
        self.conn.commit()
        self.add_event(job_id, EventKind.STAGE, f"stage -> {stage.value}", ts=ts)

    def archive_job(self, job_id: int, reason: str, ts: str) -> None:
        """Hide a job the operator judged uninteresting. Survives re-polling."""
        self.conn.execute(
            "UPDATE jobs SET dismissed = 1, dismiss_reason = ? WHERE id = ?",
            (reason, job_id),
        )
        self.conn.commit()
        self.add_event(job_id, EventKind.DISMISS, reason, ts=ts)

    def dismiss_job(self, job_id: int, reason: str, ts: str) -> None:
        """Deprecated alias for archive_job, kept for existing callers."""
        self.archive_job(job_id, reason, ts)

    def restore_job(self, job_id: int, ts: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET dismissed = 0, dismiss_reason = '' WHERE id = ?",
            (job_id,),
        )
        self.conn.commit()
        self.add_event(job_id, EventKind.NOTE, "restored from archive", ts=ts)

    def set_note(self, job_id: int, text: str, ts: str) -> None:
        """Replace the job's standing note and log the change as history."""
        self.conn.execute("UPDATE jobs SET notes = ? WHERE id = ?", (text, job_id))
        self.conn.commit()
        self.add_event(job_id, EventKind.NOTE, text, ts=ts)

    def set_priority(self, job_id: int, value: int, ts: str) -> None:
        if not MIN_PRIORITY <= value <= MAX_PRIORITY:
            raise ValueError(
                f"priority must be {MIN_PRIORITY}-{MAX_PRIORITY}, got {value}"
            )
        self.conn.execute(
            "UPDATE jobs SET priority = ? WHERE id = ?", (value, job_id)
        )
        self.conn.commit()
        self.add_event(job_id, EventKind.NOTE, f"priority -> {value}", ts=ts)

    def set_link_status(self, job_id: int, status: str, ts: str) -> None:
        """Record whether the posting URL still resolves.

        Never archives: a 404 can mean filled, moved, or a transient error, and
        silently dropping a tracked job is worse than showing a stale row.
        """
        if status not in VALID_LINK_STATUS:
            raise ValueError(f"unknown link status: {status}")
        self.conn.execute(
            "UPDATE jobs SET link_status = ?, last_checked = ? WHERE id = ?",
            (status, ts, job_id),
        )
        self.conn.commit()

    # --- events --------------------------------------------------------

    def add_event(self, job_id: int, kind: EventKind, text: str, ts: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO events (job_id, ts, kind, text) VALUES (?, ?, ?, ?)",
            (job_id, ts, kind.value, text),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_events(self, job_id: int) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE job_id = ? ORDER BY ts, id", (job_id,)
        ).fetchall()
        return [
            Event(job_id=r["job_id"], kind=EventKind(r["kind"]), text=r["text"],
                  ts=r["ts"], id=r["id"])
            for r in rows
        ]


def _row_to_employer(row: sqlite3.Row) -> Employer:
    return Employer(
        id=row["id"], name=row["name"], country=row["country"], city=row["city"],
        ats=row["ats"], ats_endpoint=row["ats_endpoint"],
        careers_url=row["careers_url"], tags=json.loads(row["tags"]),
        poll_enabled=bool(row["poll_enabled"]), last_polled=row["last_polled"],
        read_everything=bool(row["read_everything"]),
        last_manual_check=row["last_manual_check"], notes=row["notes"],
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"], employer_id=row["employer_id"], title=row["title"],
        url=row["url"], city=row["city"], country=row["country"],
        source=row["source"], snapshot_path=row["snapshot_path"],
        first_seen=row["first_seen"], last_seen=row["last_seen"],
        stage=Stage(row["stage"]), dismissed=bool(row["dismissed"]),
        dismiss_reason=row["dismiss_reason"], role_fit=row["role_fit"],
        comp_score=row["comp_score"], qol_score=row["qol_score"],
        total_score=row["total_score"], salary_stated=row["salary_stated"],
        level=row["level"], language_flags=json.loads(row["language_flags"]),
        tags=json.loads(row["tags"]), base_cv=row["base_cv"], angle=row["angle"],
        applied_on=row["applied_on"], notes=row["notes"], priority=row["priority"],
        link_status=row["link_status"], last_checked=row["last_checked"],
        description=row["description"], llm_fit=row["llm_fit"],
        llm_json=row["llm_json"], llm_checked=row["llm_checked"],
        rank_score=row["rank_score"],
    )
