from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from jobhunt.models import Employer, Event, EventKind, Job, Stage

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
    applied_on TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, ts);
CREATE INDEX IF NOT EXISTS idx_jobs_stage ON jobs(stage, dismissed);
"""

# Fields that belong to the user's tracking decisions and must never be
# overwritten when a poller re-sees a posting it has already recorded.
_PRESERVED_ON_REUPSERT = (
    "stage", "dismissed", "dismiss_reason", "base_cv", "angle",
    "applied_on", "first_seen",
)


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def initialize(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- employers -----------------------------------------------------

    def upsert_employer(self, e: Employer) -> int:
        self.conn.execute(
            """
            INSERT INTO employers
                (name, country, city, ats, ats_endpoint, careers_url, tags,
                 poll_enabled, last_polled, last_manual_check, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                country=excluded.country, city=excluded.city, ats=excluded.ats,
                ats_endpoint=excluded.ats_endpoint, careers_url=excluded.careers_url,
                tags=excluded.tags, poll_enabled=excluded.poll_enabled,
                notes=excluded.notes
            """,
            (e.name, e.country, e.city, e.ats, e.ats_endpoint, e.careers_url,
             json.dumps(e.tags), int(e.poll_enabled), e.last_polled,
             e.last_manual_check, e.notes),
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
                    salary_stated=?, level=?, language_flags=?, tags=?
                WHERE id = ?
                """,
                (j.title, j.city, j.country, j.source, j.last_seen,
                 j.snapshot_path, j.role_fit, j.comp_score, j.qol_score,
                 j.total_score, j.salary_stated, j.level,
                 json.dumps(j.language_flags), json.dumps(j.tags),
                 existing["id"]),
            )
            self.conn.commit()
            return int(existing["id"])

        cur = self.conn.execute(
            """
            INSERT INTO jobs
                (employer_id, title, url, city, country, source, snapshot_path,
                 first_seen, last_seen, stage, dismissed, dismiss_reason,
                 role_fit, comp_score, qol_score, total_score, salary_stated,
                 level, language_flags, tags, base_cv, angle, applied_on)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (j.employer_id, j.title, j.url, j.city, j.country, j.source,
             j.snapshot_path, j.first_seen, j.last_seen, j.stage.value,
             int(j.dismissed), j.dismiss_reason, j.role_fit, j.comp_score,
             j.qol_score, j.total_score, j.salary_stated, j.level,
             json.dumps(j.language_flags), json.dumps(j.tags), j.base_cv,
             j.angle, j.applied_on),
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
        include_dismissed: bool = False,
        since: str | None = None,
    ) -> list[Job]:
        clauses, params = [], []
        if not include_dismissed:
            clauses.append("dismissed = 0")
        if stage is not None:
            clauses.append("stage = ?")
            params.append(stage.value)
        if since is not None:
            clauses.append("first_seen >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY total_score DESC, id DESC", params
        ).fetchall()
        return [_row_to_job(r) for r in rows]

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

    def dismiss_job(self, job_id: int, reason: str, ts: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET dismissed = 1, dismiss_reason = ? WHERE id = ?",
            (reason, job_id),
        )
        self.conn.commit()
        self.add_event(job_id, EventKind.DISMISS, reason, ts=ts)

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
        applied_on=row["applied_on"],
    )
