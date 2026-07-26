# Job Tracker Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local CLI that tracks GNSS/PNT/radar job opportunities through an application pipeline, scores them on compensation, quality of life, and role fit, and renders a static HTML dashboard.

**Architecture:** A Python package with a SQLite store at the centre. Pure functions handle matching and scoring over YAML config; a thin store layer handles persistence; a Typer CLI wires them together; Jinja2 renders a static dashboard. Job discovery goes through a `Source` protocol — this plan implements only the manual source, leaving ATS pollers to a follow-up plan.

**Tech Stack:** Python 3.11+, SQLite (stdlib `sqlite3`), Typer, httpx, Jinja2, PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-07-26-job-application-tracker-design.md`

**Out of scope for this plan** (deferred to a second plan after the ATS discovery pass): `sources/workday.py`, `sources/greenhouse.py`, `sources/smartrecruiters.py`, `sources/successfactors.py`, `sources/euraxess.py`, and the `jobs poll` command. The `Source` protocol and `RawPosting` type are defined here so those modules drop in without refactoring.

## Global Constraints

- Python 3.11 or later. Use `from __future__ import annotations` in every module.
- Dependencies limited to: `typer`, `httpx`, `jinja2`, `pyyaml`. Test-only: `pytest`.
- No network access in the test suite. All HTTP is injected and mocked.
- All thresholds, weights, and keyword lists live in `config/*.yaml`. Nothing hardcoded in source.
- Scoring sorts; it never filters. Jobs below threshold are stored and viewable, never discarded.
- Currency is EUR throughout. Salaries are annual gross unless a field name says otherwise.
- All timestamps stored as ISO 8601 UTC strings. All dates as ISO `YYYY-MM-DD`.
- `today` is always an injected parameter in date-dependent functions, never `date.today()` inside logic — this keeps tests deterministic.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, dependencies, pytest config |
| `src/jobhunt/models.py` | Dataclasses and enums shared by every module |
| `src/jobhunt/store.py` | SQLite schema and all persistence |
| `src/jobhunt/config.py` | Load and validate the YAML config files |
| `src/jobhunt/match.py` | Hard gates, role-family fit, negative keywords, language flags |
| `src/jobhunt/score.py` | Compensation, quality of life, weighted total |
| `src/jobhunt/sources/base.py` | `Source` protocol and `RawPosting` |
| `src/jobhunt/sources/manual.py` | Build a `RawPosting` from a pasted URL |
| `src/jobhunt/snapshot.py` | Fetch a URL, strip HTML to text, save to `data/postings/` |
| `src/jobhunt/staleness.py` | Follow-up nudges from event history |
| `src/jobhunt/deadlines.py` | Upcoming recurring application cycles |
| `src/jobhunt/report.py` | Build dashboard context and render HTML |
| `src/jobhunt/templates/dashboard.html.j2` | Dashboard template |
| `src/jobhunt/cli.py` | Typer commands |
| `config/*.yaml` | Employers, cities, compensation, scoring, deadlines |
| `profile/` | User-authored positioning content |

---

### Task 1: Project scaffolding and shared models

**Files:**
- Create: `pyproject.toml`
- Create: `src/jobhunt/__init__.py`
- Create: `src/jobhunt/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Stage` (str enum), `TERMINAL_STAGES`, `is_terminal(stage) -> bool`, `EventKind` (str enum), and dataclasses `Employer`, `Job`, `Event`, `Deadline`. Every later task imports from here.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "jobhunt"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["typer>=0.12", "httpx>=0.27", "jinja2>=3.1", "pyyaml>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
jobs = "jobhunt.cli:app"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
jobhunt = ["templates/*.j2"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create the package directory and install**

```bash
mkdir -p src/jobhunt/sources src/jobhunt/templates tests
touch src/jobhunt/__init__.py src/jobhunt/sources/__init__.py
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_models.py`:

```python
from jobhunt.models import Stage, EventKind, is_terminal, Job


def test_terminal_stages_are_terminal():
    assert is_terminal(Stage.REJECTED)
    assert is_terminal(Stage.WITHDRAWN)
    assert is_terminal(Stage.EXPIRED)


def test_active_stages_are_not_terminal():
    assert not is_terminal(Stage.SPOTTED)
    assert not is_terminal(Stage.APPLIED)
    assert not is_terminal(Stage.OFFER)


def test_stage_serialises_to_its_string_value():
    assert Stage.APPLIED.value == "applied"
    assert Stage("applied") is Stage.APPLIED


def test_event_kinds_exist():
    assert EventKind.STAGE.value == "stage"
    assert EventKind.NOTE.value == "note"
    assert EventKind.FOLLOWUP.value == "followup"
    assert EventKind.DISMISS.value == "dismiss"


def test_job_defaults_to_spotted_and_not_dismissed():
    job = Job(employer_id=1, title="GNSS Engineer", url="https://example.com/1")
    assert job.stage is Stage.SPOTTED
    assert job.dismissed is False
    assert job.tags == []
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobhunt.models'`

- [ ] **Step 5: Write `src/jobhunt/models.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Stage(str, Enum):
    SPOTTED = "spotted"
    SHORTLISTED = "shortlisted"
    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


TERMINAL_STAGES = frozenset({Stage.REJECTED, Stage.WITHDRAWN, Stage.EXPIRED})


def is_terminal(stage: Stage) -> bool:
    """True when no further action is expected on a job at this stage."""
    return stage in TERMINAL_STAGES


class EventKind(str, Enum):
    STAGE = "stage"
    NOTE = "note"
    FOLLOWUP = "followup"
    DISMISS = "dismiss"


@dataclass
class Employer:
    name: str
    country: str = ""
    city: str = ""
    ats: str = "manual"
    ats_endpoint: str = ""
    careers_url: str = ""
    tags: list[str] = field(default_factory=list)
    poll_enabled: bool = False
    last_polled: str | None = None
    last_manual_check: str | None = None
    notes: str = ""
    id: int | None = None


@dataclass
class Job:
    employer_id: int
    title: str
    url: str
    city: str = ""
    country: str = ""
    source: str = "manual"
    snapshot_path: str = ""
    first_seen: str | None = None
    last_seen: str | None = None
    stage: Stage = Stage.SPOTTED
    dismissed: bool = False
    dismiss_reason: str = ""
    role_fit: float = 0.0
    comp_score: float = 0.0
    qol_score: float = 0.0
    total_score: float = 0.0
    salary_stated: float | None = None
    level: str = "junior"
    language_flags: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    base_cv: str = ""
    angle: str = ""
    applied_on: str | None = None
    id: int | None = None


@dataclass
class Event:
    job_id: int
    kind: EventKind
    text: str
    ts: str | None = None
    id: int | None = None


@dataclass
class Deadline:
    name: str
    employer: str = ""
    opens: str | None = None
    closes: str | None = None
    url: str = ""
    lead_days: int = 42
    notes: str = ""
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: PASS, 5 tests

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/jobhunt tests/test_models.py
git commit -m "feat: add package scaffolding and shared models"
```

---

### Task 2: SQLite store

**Files:**
- Create: `src/jobhunt/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Employer`, `Job`, `Event`, `Stage`, `EventKind` from `jobhunt.models`.
- Produces: `Store(path: Path)` with methods `initialize()`, `upsert_employer(Employer) -> int`, `get_employer(int) -> Employer | None`, `find_employer_by_name(str) -> Employer | None`, `list_employers() -> list[Employer]`, `upsert_job(Job) -> int`, `get_job(int) -> Job | None`, `list_jobs(*, stage=None, include_dismissed=False, since=None) -> list[Job]`, `set_stage(int, Stage, ts) -> None`, `dismiss_job(int, str, ts) -> None`, `add_event(int, EventKind, str, ts) -> int`, `list_events(int) -> list[Event]`, `close() -> None`.

`upsert_job` deduplicates on `url`: an existing URL updates `last_seen` and leaves stage, dismissal, and application fields untouched. This is what makes re-polling safe and makes dismissals permanent.

- [ ] **Step 1: Write the failing test**

Create `tests/test_store.py`:

```python
import pytest

from jobhunt.models import Employer, EventKind, Job, Stage
from jobhunt.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    s.initialize()
    yield s
    s.close()


def test_employer_roundtrip(store):
    eid = store.upsert_employer(
        Employer(name="Septentrio", country="BE", city="Leuven", tags=["gnss"])
    )
    got = store.get_employer(eid)
    assert got is not None
    assert got.name == "Septentrio"
    assert got.tags == ["gnss"]


def test_upsert_employer_by_name_updates_not_duplicates(store):
    first = store.upsert_employer(Employer(name="GMV", country="ES"))
    second = store.upsert_employer(Employer(name="GMV", country="ES", city="Madrid"))
    assert first == second
    assert len(store.list_employers()) == 1
    assert store.get_employer(first).city == "Madrid"


def test_job_roundtrip(store):
    eid = store.upsert_employer(Employer(name="u-blox"))
    jid = store.upsert_job(
        Job(employer_id=eid, title="GNSS Engineer", url="https://x/1",
            language_flags=["de"], first_seen="2026-07-26T10:00:00Z")
    )
    got = store.get_job(jid)
    assert got.title == "GNSS Engineer"
    assert got.stage is Stage.SPOTTED
    assert got.language_flags == ["de"]


def test_upsert_job_on_same_url_preserves_stage_and_updates_last_seen(store):
    eid = store.upsert_employer(Employer(name="DLR"))
    jid = store.upsert_job(
        Job(employer_id=eid, title="PNT Engineer", url="https://x/2",
            first_seen="2026-07-01T00:00:00Z", last_seen="2026-07-01T00:00:00Z")
    )
    store.set_stage(jid, Stage.APPLIED, ts="2026-07-10T00:00:00Z")

    again = store.upsert_job(
        Job(employer_id=eid, title="PNT Engineer", url="https://x/2",
            last_seen="2026-07-20T00:00:00Z")
    )
    assert again == jid
    job = store.get_job(jid)
    assert job.stage is Stage.APPLIED
    assert job.last_seen == "2026-07-20T00:00:00Z"
    assert job.first_seen == "2026-07-01T00:00:00Z"


def test_dismissed_jobs_hidden_by_default_and_survive_reupsert(store):
    eid = store.upsert_employer(Employer(name="Noise Corp"))
    jid = store.upsert_job(Job(employer_id=eid, title="Land Surveyor", url="https://x/3"))
    store.dismiss_job(jid, "surveying, not engineering", ts="2026-07-26T00:00:00Z")

    assert store.list_jobs() == []
    assert len(store.list_jobs(include_dismissed=True)) == 1

    store.upsert_job(Job(employer_id=eid, title="Land Surveyor", url="https://x/3"))
    assert store.list_jobs() == []
    assert store.get_job(jid).dismiss_reason == "surveying, not engineering"


def test_list_jobs_filters_by_stage(store):
    eid = store.upsert_employer(Employer(name="Thales"))
    a = store.upsert_job(Job(employer_id=eid, title="A", url="https://x/a"))
    store.upsert_job(Job(employer_id=eid, title="B", url="https://x/b"))
    store.set_stage(a, Stage.APPLIED, ts="2026-07-26T00:00:00Z")

    applied = store.list_jobs(stage=Stage.APPLIED)
    assert [j.title for j in applied] == ["A"]


def test_events_are_appended_and_ordered(store):
    eid = store.upsert_employer(Employer(name="ESA"))
    jid = store.upsert_job(Job(employer_id=eid, title="YGT", url="https://x/y"))
    store.add_event(jid, EventKind.NOTE, "emailed the group", ts="2026-07-02T00:00:00Z")
    store.add_event(jid, EventKind.NOTE, "no reply", ts="2026-07-20T00:00:00Z")

    events = store.list_events(jid)
    assert [e.text for e in events] == ["emailed the group", "no reply"]


def test_set_stage_records_an_event(store):
    eid = store.upsert_employer(Employer(name="Safran"))
    jid = store.upsert_job(Job(employer_id=eid, title="Nav Engineer", url="https://x/s"))
    store.set_stage(jid, Stage.APPLIED, ts="2026-07-26T00:00:00Z")

    events = store.list_events(jid)
    assert len(events) == 1
    assert events[0].kind is EventKind.STAGE
    assert "applied" in events[0].text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobhunt.store'`

- [ ] **Step 3: Write `src/jobhunt/store.py`**

```python
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
```

Note: `_PRESERVED_ON_REUPSERT` documents the invariant the `UPDATE` in `upsert_job` implements — those columns are deliberately absent from the `SET` clause.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add src/jobhunt/store.py tests/test_store.py
git commit -m "feat: add SQLite store with dedupe-safe job upsert"
```

---

### Task 3: Config loading

**Files:**
- Create: `src/jobhunt/config.py`
- Create: `config/scoring.yaml`
- Create: `config/cities.yaml`
- Create: `config/comp.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `Employer`, `Deadline` from `jobhunt.models`.
- Produces: dataclasses `RoleFamily(name, weight, keywords)`, `ScoringConfig(weights, qol_weights, role_families, negative_keywords, excluded_countries, known_languages, language_keywords, staleness, sunshine_range, rent_range)`, `City(name, country, sunshine_hours, nature, rent_index)`, `CompConfig(salary_by_country, effective_tax, pli, reference_purchasing_power)`; loaders `load_scoring(Path) -> ScoringConfig`, `load_cities(Path) -> dict[str, City]`, `load_comp(Path) -> CompConfig`, `load_employers(Path) -> list[Employer]`, `load_deadlines(Path) -> list[Deadline]`.

City lookup keys are lowercased city names.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import textwrap

import pytest

from jobhunt.config import (
    load_cities, load_comp, load_deadlines, load_employers, load_scoring,
)


def write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return p


def test_load_scoring(tmp_path):
    p = write(tmp_path, "scoring.yaml", """
        weights: {comp: 0.3, qol: 0.2, fit: 0.5}
        qol_weights: {sunshine: 0.5, nature: 0.3, rent: 0.2}
        sunshine_range: [1300, 2900]
        rent_range: [400, 1800]
        excluded_countries: [GB]
        known_languages: [en, fr, es, pt]
        language_keywords:
          de: ["german", "deutsch"]
        negative_keywords: ["land surveyor", "sales"]
        staleness:
          applied: [14, 30]
          phd: [21, 45]
        role_families:
          - name: gnss
            weight: 1.0
            keywords: ["gnss", "galileo", "receiver"]
          - name: radar
            weight: 0.7
            keywords: ["radar"]
    """)
    cfg = load_scoring(p)
    assert cfg.weights["fit"] == 0.5
    assert cfg.excluded_countries == ["GB"]
    assert cfg.staleness["applied"] == [14, 30]
    assert cfg.role_families[0].name == "gnss"
    assert "galileo" in cfg.role_families[0].keywords
    assert cfg.role_families[1].weight == 0.7


def test_scoring_weights_must_be_present(tmp_path):
    p = write(tmp_path, "scoring.yaml", "weights: {comp: 0.5}\n")
    with pytest.raises(ValueError, match="weights"):
        load_scoring(p)


def test_load_cities_keys_are_lowercased(tmp_path):
    p = write(tmp_path, "cities.yaml", """
        cities:
          - name: Munich
            country: DE
            sunshine_hours: 1777
            nature: 9
            rent_index: 1400
          - name: Toulouse
            country: FR
            sunshine_hours: 2100
            nature: 7
            rent_index: 750
    """)
    cities = load_cities(p)
    assert cities["munich"].sunshine_hours == 1777
    assert cities["toulouse"].country == "FR"


def test_load_comp(tmp_path):
    p = write(tmp_path, "comp.yaml", """
        reference_purchasing_power: 40000
        effective_tax: {DE: 0.36, FR: 0.28}
        pli: {DE: 1.07, FR: 1.03}
        salary_by_country:
          DE: {junior: 58000, phd: 33000}
          FR: {junior: 41000, phd: 24000}
    """)
    cfg = load_comp(p)
    assert cfg.salary_by_country["DE"]["junior"] == 58000
    assert cfg.effective_tax["FR"] == 0.28
    assert cfg.reference_purchasing_power == 40000


def test_load_employers(tmp_path):
    p = write(tmp_path, "employers.yaml", """
        employers:
          - name: Septentrio
            country: BE
            city: Leuven
            ats: manual
            careers_url: https://example.com/careers
            tags: [gnss]
    """)
    employers = load_employers(p)
    assert employers[0].name == "Septentrio"
    assert employers[0].tags == ["gnss"]
    assert employers[0].poll_enabled is False


def test_load_deadlines(tmp_path):
    p = write(tmp_path, "deadlines.yaml", """
        deadlines:
          - name: ESA Young Graduate Trainee
            employer: ESA
            opens: 2026-09-01
            closes: 2026-11-15
            url: https://example.com/ygt
            lead_days: 60
    """)
    d = load_deadlines(p)
    assert d[0].name == "ESA Young Graduate Trainee"
    assert d[0].closes == "2026-11-15"
    assert d[0].lead_days == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobhunt.config'`

- [ ] **Step 3: Write `src/jobhunt/config.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from jobhunt.models import Deadline, Employer

REQUIRED_WEIGHTS = ("comp", "qol", "fit")


@dataclass
class RoleFamily:
    name: str
    weight: float
    keywords: list[str]


@dataclass
class ScoringConfig:
    weights: dict[str, float]
    qol_weights: dict[str, float]
    role_families: list[RoleFamily]
    negative_keywords: list[str] = field(default_factory=list)
    excluded_countries: list[str] = field(default_factory=list)
    known_languages: list[str] = field(default_factory=list)
    language_keywords: dict[str, list[str]] = field(default_factory=dict)
    staleness: dict[str, list[int]] = field(default_factory=dict)
    sunshine_range: list[int] = field(default_factory=lambda: [1300, 2900])
    rent_range: list[int] = field(default_factory=lambda: [400, 1800])


@dataclass
class City:
    name: str
    country: str
    sunshine_hours: float
    nature: float
    rent_index: float


@dataclass
class CompConfig:
    salary_by_country: dict[str, dict[str, float]]
    effective_tax: dict[str, float]
    pli: dict[str, float]
    reference_purchasing_power: float


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_scoring(path: Path) -> ScoringConfig:
    raw = _read(path)
    weights = raw.get("weights", {})
    missing = [k for k in REQUIRED_WEIGHTS if k not in weights]
    if missing:
        raise ValueError(f"scoring config missing weights: {', '.join(missing)}")
    families = [
        RoleFamily(name=f["name"], weight=float(f.get("weight", 1.0)),
                   keywords=[k.lower() for k in f.get("keywords", [])])
        for f in raw.get("role_families", [])
    ]
    return ScoringConfig(
        weights={k: float(v) for k, v in weights.items()},
        qol_weights={k: float(v) for k, v in raw.get("qol_weights", {}).items()},
        role_families=families,
        negative_keywords=[k.lower() for k in raw.get("negative_keywords", [])],
        excluded_countries=raw.get("excluded_countries", []),
        known_languages=raw.get("known_languages", []),
        language_keywords={
            lang: [k.lower() for k in kws]
            for lang, kws in raw.get("language_keywords", {}).items()
        },
        staleness=raw.get("staleness", {}),
        sunshine_range=raw.get("sunshine_range", [1300, 2900]),
        rent_range=raw.get("rent_range", [400, 1800]),
    )


def load_cities(path: Path) -> dict[str, City]:
    raw = _read(path)
    out: dict[str, City] = {}
    for c in raw.get("cities", []):
        city = City(
            name=c["name"], country=c.get("country", ""),
            sunshine_hours=float(c.get("sunshine_hours", 0)),
            nature=float(c.get("nature", 0)),
            rent_index=float(c.get("rent_index", 0)),
        )
        out[city.name.lower()] = city
    return out


def load_comp(path: Path) -> CompConfig:
    raw = _read(path)
    return CompConfig(
        salary_by_country={
            country: {lvl: float(v) for lvl, v in levels.items()}
            for country, levels in raw.get("salary_by_country", {}).items()
        },
        effective_tax={k: float(v) for k, v in raw.get("effective_tax", {}).items()},
        pli={k: float(v) for k, v in raw.get("pli", {}).items()},
        reference_purchasing_power=float(raw.get("reference_purchasing_power", 40000)),
    )


def load_employers(path: Path) -> list[Employer]:
    raw = _read(path)
    return [
        Employer(
            name=e["name"], country=e.get("country", ""), city=e.get("city", ""),
            ats=e.get("ats", "manual"), ats_endpoint=e.get("ats_endpoint", ""),
            careers_url=e.get("careers_url", ""), tags=e.get("tags", []),
            poll_enabled=bool(e.get("poll_enabled", False)),
            notes=e.get("notes", ""),
        )
        for e in raw.get("employers", [])
    ]


def load_deadlines(path: Path) -> list[Deadline]:
    raw = _read(path)
    return [
        Deadline(
            name=d["name"], employer=d.get("employer", ""),
            opens=str(d["opens"]) if d.get("opens") else None,
            closes=str(d["closes"]) if d.get("closes") else None,
            url=d.get("url", ""), lead_days=int(d.get("lead_days", 42)),
            notes=d.get("notes", ""),
        )
        for d in raw.get("deadlines", [])
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Create the real config files**

Create `config/scoring.yaml`:

```yaml
weights: {comp: 0.30, qol: 0.20, fit: 0.50}
qol_weights: {sunshine: 0.45, nature: 0.35, rent: 0.20}
sunshine_range: [1300, 2900]
rent_range: [400, 1800]

excluded_countries: [GB]
known_languages: [en, fr, es, pt]
language_keywords:
  de: ["german language", "deutschkenntnisse", "fluent german", "german is required"]
  nl: ["dutch language", "fluent dutch"]

negative_keywords:
  - "land surveyor"
  - "surveying technician"
  - "gis technician"
  - "account manager"
  - "sales representative"
  - "field service"
  - "business development"

staleness:
  applied: [14, 30]
  phd: [21, 45]

role_families:
  - name: gnss
    weight: 1.0
    keywords: ["gnss", "gps", "galileo", "receiver", "signal processing",
               "correlator", "acquisition and tracking", "rtk", "ppp"]
  - name: pnt
    weight: 1.0
    keywords: ["pnt", "integrity", "spoofing", "jamming", "interference",
               "anti-jam", "raim", "resilient navigation", "timing"]
  - name: navigation_systems
    weight: 0.9
    keywords: ["navigation system", "avionics", "guidance navigation and control",
               "gnc", "flight dynamics", "orbit determination"]
  - name: sensor_fusion
    weight: 0.9
    keywords: ["sensor fusion", "kalman", "estimation", "inertial", "imu",
               "ins", "state estimation", "slam"]
  - name: radar
    weight: 0.8
    keywords: ["radar", "sar", "doppler", "waveform", "beamforming",
               "electromagnetic", "rf front-end"]
  - name: adjacent
    weight: 0.5
    keywords: ["autonomous driving", "geodesy", "remote sensing",
               "digital signal processing", "software defined radio"]
```

Create `config/cities.yaml`. Values below are starting estimates — annual sunshine
hours from published climate normals, `nature` a personal 0–10 score for outdoor
access, `rent_index` a monthly one-bedroom figure in EUR. **Correct these as you
learn real numbers; they are inputs you own, not facts the tool asserts.**

```yaml
cities:
  - {name: Munich,     country: DE, sunshine_hours: 1777, nature: 9, rent_index: 1400}
  - {name: Toulouse,   country: FR, sunshine_hours: 2100, nature: 7, rent_index: 750}
  - {name: Noordwijk,  country: NL, sunshine_hours: 1660, nature: 7, rent_index: 1300}
  - {name: Delft,      country: NL, sunshine_hours: 1660, nature: 5, rent_index: 1250}
  - {name: Leuven,     country: BE, sunshine_hours: 1620, nature: 5, rent_index: 900}
  - {name: Brussels,   country: BE, sunshine_hours: 1550, nature: 4, rent_index: 1100}
  - {name: Zurich,     country: CH, sunshine_hours: 1660, nature: 9, rent_index: 2100}
  - {name: Thalwil,    country: CH, sunshine_hours: 1660, nature: 9, rent_index: 1900}
  - {name: Madrid,     country: ES, sunshine_hours: 2769, nature: 7, rent_index: 1100}
  - {name: Barcelona,  country: ES, sunshine_hours: 2524, nature: 8, rent_index: 1200}
  - {name: Rome,       country: IT, sunshine_hours: 2473, nature: 6, rent_index: 950}
  - {name: Turin,      country: IT, sunshine_hours: 2000, nature: 9, rent_index: 700}
  - {name: Milan,      country: IT, sunshine_hours: 1915, nature: 7, rent_index: 1200}
  - {name: Padua,      country: IT, sunshine_hours: 2000, nature: 6, rent_index: 700}
  - {name: Nuremberg,  country: DE, sunshine_hours: 1680, nature: 7, rent_index: 850}
  - {name: Darmstadt,  country: DE, sunshine_hours: 1650, nature: 6, rent_index: 900}
  - {name: Braunschweig, country: DE, sunshine_hours: 1550, nature: 5, rent_index: 700}
  - {name: Toulon,     country: FR, sunshine_hours: 2800, nature: 9, rent_index: 800}
  - {name: Nice,       country: FR, sunshine_hours: 2724, nature: 9, rent_index: 1150}
  - {name: Paris,      country: FR, sunshine_hours: 1662, nature: 3, rent_index: 1350}
  - {name: Lisbon,     country: PT, sunshine_hours: 2799, nature: 8, rent_index: 1100}
  - {name: Gothenburg, country: SE, sunshine_hours: 1700, nature: 9, rent_index: 950}
```

Create `config/comp.yaml`. **All figures are starting estimates to be corrected as
real numbers emerge.** PhD figures for DE approximate TV-L E13 at 65%; FR
approximates the national *contrat doctoral* rate. These two are the closest to
being genuinely known.

```yaml
reference_purchasing_power: 40000

# Effective all-in rate for a single filer with no dependants, incl. social
# contributions. Approximate; sufficient for ranking, not for planning.
effective_tax:
  DE: 0.38
  FR: 0.26
  NL: 0.33
  BE: 0.41
  CH: 0.18
  IT: 0.33
  ES: 0.27
  PT: 0.31
  SE: 0.32

# Eurostat price level index, EU27 = 1.00. Update from the annual release.
pli:
  DE: 1.07
  FR: 1.09
  NL: 1.15
  BE: 1.11
  CH: 1.55
  IT: 1.00
  ES: 0.93
  PT: 0.90
  SE: 1.20

salary_by_country:
  DE: {junior: 58000, phd: 33000}
  FR: {junior: 41000, phd: 24000}
  NL: {junior: 46000, phd: 34000}
  BE: {junior: 44000, phd: 32000}
  CH: {junior: 92000, phd: 50000}
  IT: {junior: 32000, phd: 20000}
  ES: {junior: 30000, phd: 19000}
  PT: {junior: 26000, phd: 18000}
  SE: {junior: 45000, phd: 33000}
```

- [ ] **Step 6: Verify the real configs load**

Run: `.venv/bin/python -c "from pathlib import Path; from jobhunt.config import *; s=load_scoring(Path('config/scoring.yaml')); c=load_cities(Path('config/cities.yaml')); m=load_comp(Path('config/comp.yaml')); print(len(s.role_families), len(c), len(m.pli))"`
Expected: `6 22 9`

- [ ] **Step 7: Commit**

```bash
git add src/jobhunt/config.py tests/test_config.py config/
git commit -m "feat: add YAML config loading with seed scoring, city and comp data"
```

---

### Task 4: Matching

**Files:**
- Create: `src/jobhunt/match.py`
- Test: `tests/test_match.py`

**Interfaces:**
- Consumes: `ScoringConfig`, `RoleFamily` from `jobhunt.config`.
- Produces: `MatchResult(relevant: bool, role_fit: float, matched_families: list[str], language_flags: list[str], reasons: list[str])` and `evaluate(title: str, description: str, country: str, cfg: ScoringConfig) -> MatchResult`.

Rules, in order: excluded country is a hard gate; a negative keyword in the title is a hard gate (description-only hits are not, since job descriptions often mention adjacent teams); role fit is the highest-scoring family, with title hits weighted double; language keywords for languages outside `known_languages` produce flags, never rejections.

- [ ] **Step 1: Write the failing test**

Create `tests/test_match.py`:

```python
import pytest

from jobhunt.config import RoleFamily, ScoringConfig
from jobhunt.match import evaluate


@pytest.fixture
def cfg():
    return ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[
            RoleFamily("gnss", 1.0, ["gnss", "galileo", "receiver"]),
            RoleFamily("radar", 0.8, ["radar", "sar"]),
            RoleFamily("adjacent", 0.5, ["digital signal processing"]),
        ],
        negative_keywords=["land surveyor", "sales representative"],
        excluded_countries=["GB"],
        known_languages=["en", "fr", "es", "pt"],
        language_keywords={"de": ["fluent german"], "nl": ["fluent dutch"]},
    )


def test_excluded_country_is_rejected(cfg):
    r = evaluate("GNSS Engineer", "great job", "GB", cfg)
    assert r.relevant is False
    assert any("GB" in reason for reason in r.reasons)


def test_negative_keyword_in_title_is_rejected(cfg):
    r = evaluate("Land Surveyor", "gnss equipment", "DE", cfg)
    assert r.relevant is False
    assert any("land surveyor" in reason for reason in r.reasons)


def test_negative_keyword_only_in_description_is_not_rejected(cfg):
    r = evaluate("GNSS Engineer", "you will support our sales representative", "DE", cfg)
    assert r.relevant is True


def test_title_match_scores_higher_than_description_match(cfg):
    in_title = evaluate("GNSS Receiver Engineer", "unrelated text", "DE", cfg)
    in_desc = evaluate("Systems Engineer", "work on gnss receiver design", "DE", cfg)
    assert in_title.role_fit > in_desc.role_fit


def test_role_fit_uses_the_best_family_weighted(cfg):
    gnss = evaluate("GNSS Engineer", "", "DE", cfg)
    adjacent = evaluate("Digital Signal Processing Engineer", "", "DE", cfg)
    assert gnss.role_fit > adjacent.role_fit
    assert gnss.matched_families == ["gnss"]


def test_role_fit_is_clamped_to_unit_interval(cfg):
    r = evaluate("GNSS Galileo Receiver Engineer",
                 "gnss galileo receiver gnss galileo receiver", "DE", cfg)
    assert 0.0 <= r.role_fit <= 1.0


def test_no_match_is_irrelevant_but_scored_zero(cfg):
    r = evaluate("Accountant", "bookkeeping", "DE", cfg)
    assert r.role_fit == 0.0
    assert r.relevant is False
    assert any("no role family" in reason for reason in r.reasons)


def test_unknown_language_is_flagged_not_rejected(cfg):
    r = evaluate("GNSS Engineer", "fluent German required", "DE", cfg)
    assert r.relevant is True
    assert r.language_flags == ["de"]


def test_known_language_is_not_flagged(cfg):
    cfg.language_keywords["fr"] = ["fluent french"]
    r = evaluate("GNSS Engineer", "fluent French required", "FR", cfg)
    assert r.language_flags == []


def test_matching_is_case_insensitive(cfg):
    r = evaluate("GALILEO SIGNAL ENGINEER", "", "DE", cfg)
    assert r.role_fit > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_match.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobhunt.match'`

- [ ] **Step 3: Write `src/jobhunt/match.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field

from jobhunt.config import RoleFamily, ScoringConfig

TITLE_WEIGHT = 2.0
DESCRIPTION_WEIGHT = 1.0
# Hits needed in the title alone for a family to reach its full weight.
SATURATION = 2.0


@dataclass
class MatchResult:
    relevant: bool
    role_fit: float
    matched_families: list[str] = field(default_factory=list)
    language_flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _family_score(family: RoleFamily, title: str, description: str) -> float:
    hits = 0.0
    for keyword in family.keywords:
        if keyword in title:
            hits += TITLE_WEIGHT
        elif keyword in description:
            hits += DESCRIPTION_WEIGHT
    if hits == 0:
        return 0.0
    saturated = min(hits / (TITLE_WEIGHT * SATURATION), 1.0)
    return saturated * family.weight


def evaluate(
    title: str, description: str, country: str, cfg: ScoringConfig
) -> MatchResult:
    """Score a posting for relevance. Never mutates cfg; safe to call repeatedly."""
    title_l = (title or "").lower()
    desc_l = (description or "").lower()
    reasons: list[str] = []

    if country and country.upper() in {c.upper() for c in cfg.excluded_countries}:
        return MatchResult(
            relevant=False, role_fit=0.0,
            reasons=[f"excluded country: {country.upper()}"],
        )

    for negative in cfg.negative_keywords:
        if negative in title_l:
            return MatchResult(
                relevant=False, role_fit=0.0,
                reasons=[f"negative keyword in title: {negative}"],
            )

    scores = {f.name: _family_score(f, title_l, desc_l) for f in cfg.role_families}
    matched = sorted(
        (name for name, s in scores.items() if s > 0),
        key=lambda name: scores[name], reverse=True,
    )
    role_fit = max(scores.values()) if scores else 0.0
    role_fit = min(max(role_fit, 0.0), 1.0)

    language_flags = []
    known = {lang.lower() for lang in cfg.known_languages}
    for lang, keywords in cfg.language_keywords.items():
        if lang.lower() in known:
            continue
        if any(k in title_l or k in desc_l for k in keywords):
            language_flags.append(lang)

    if role_fit == 0.0:
        reasons.append("no role family matched")

    return MatchResult(
        relevant=role_fit > 0.0,
        role_fit=role_fit,
        matched_families=matched,
        language_flags=sorted(language_flags),
        reasons=reasons,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_match.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add src/jobhunt/match.py tests/test_match.py
git commit -m "feat: add rule-based posting matcher with language flags"
```

---

### Task 5: Scoring

**Files:**
- Create: `src/jobhunt/score.py`
- Test: `tests/test_score.py`

**Interfaces:**
- Consumes: `City`, `CompConfig`, `ScoringConfig` from `jobhunt.config`.
- Produces: `CompBreakdown(gross, net, purchasing_power, source, normalised)` and functions `compensation(country, level, salary_stated, comp, city, cfg) -> CompBreakdown`, `quality_of_life(city, cfg) -> float`, `total_score(comp_norm, qol, role_fit, weights) -> float`.

`source` is one of `"stated"`, `"estimated"`, `"unknown"`. When unknown, `normalised` is 0.0 and the dashboard shows "no data" rather than implying a low salary.

- [ ] **Step 1: Write the failing test**

Create `tests/test_score.py`:

```python
import pytest

from jobhunt.config import City, CompConfig, RoleFamily, ScoringConfig
from jobhunt.score import compensation, quality_of_life, total_score


@pytest.fixture
def comp_cfg():
    return CompConfig(
        salary_by_country={"DE": {"junior": 60000, "phd": 33000},
                           "FR": {"junior": 40000}},
        effective_tax={"DE": 0.40, "FR": 0.25},
        pli={"DE": 1.00, "FR": 1.00},
        reference_purchasing_power=40000,
    )


@pytest.fixture
def cfg():
    return ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[RoleFamily("gnss", 1.0, ["gnss"])],
        sunshine_range=[1500, 2500],
        rent_range=[500, 1500],
    )


def test_stated_salary_beats_the_estimate_table(comp_cfg, cfg):
    b = compensation("DE", "junior", 75000, comp_cfg, None, cfg)
    assert b.gross == 75000
    assert b.source == "stated"


def test_estimated_salary_used_when_none_stated(comp_cfg, cfg):
    b = compensation("DE", "junior", None, comp_cfg, None, cfg)
    assert b.gross == 60000
    assert b.source == "estimated"


def test_unknown_country_yields_unknown_not_zero(comp_cfg, cfg):
    b = compensation("JP", "junior", None, comp_cfg, None, cfg)
    assert b.source == "unknown"
    assert b.gross is None
    assert b.normalised == 0.0


def test_net_applies_the_effective_rate(comp_cfg, cfg):
    b = compensation("DE", "junior", 60000, comp_cfg, None, cfg)
    assert b.net == pytest.approx(36000)


def test_purchasing_power_divides_by_price_level(comp_cfg, cfg):
    comp_cfg.pli["DE"] = 1.20
    b = compensation("DE", "junior", 60000, comp_cfg, None, cfg)
    assert b.purchasing_power == pytest.approx(30000)


def test_purchasing_power_normalises_against_the_reference(comp_cfg, cfg):
    b = compensation("DE", "junior", 100000, comp_cfg, None, cfg)
    assert b.purchasing_power == pytest.approx(60000)
    assert b.normalised == 1.0  # clamped


def test_phd_level_uses_the_phd_row(comp_cfg, cfg):
    b = compensation("DE", "phd", None, comp_cfg, None, cfg)
    assert b.gross == 33000


def test_missing_level_for_known_country_is_unknown(comp_cfg, cfg):
    b = compensation("FR", "phd", None, comp_cfg, None, cfg)
    assert b.source == "unknown"


def test_quality_of_life_rewards_sun_nature_and_cheap_rent(cfg):
    sunny = City("Lisbon", "PT", sunshine_hours=2500, nature=10, rent_index=500)
    grey = City("Grey", "XX", sunshine_hours=1500, nature=0, rent_index=1500)
    assert quality_of_life(sunny, cfg) == pytest.approx(1.0)
    assert quality_of_life(grey, cfg) == pytest.approx(0.0)


def test_quality_of_life_clamps_outside_the_configured_range(cfg):
    extreme = City("Sun", "XX", sunshine_hours=4000, nature=99, rent_index=0)
    assert quality_of_life(extreme, cfg) == pytest.approx(1.0)


def test_unknown_city_scores_mid_not_zero(cfg):
    assert quality_of_life(None, cfg) == pytest.approx(0.5)


def test_total_is_the_weighted_sum(cfg):
    assert total_score(1.0, 0.0, 1.0, cfg.weights) == pytest.approx(0.8)
    assert total_score(0.0, 1.0, 0.0, cfg.weights) == pytest.approx(0.2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_score.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobhunt.score'`

- [ ] **Step 3: Write `src/jobhunt/score.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from jobhunt.config import City, CompConfig, ScoringConfig

UNKNOWN_CITY_QOL = 0.5


@dataclass
class CompBreakdown:
    gross: float | None
    net: float | None
    purchasing_power: float | None
    source: str  # "stated" | "estimated" | "unknown"
    normalised: float


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _scale(value: float, low: float, high: float) -> float:
    if high == low:
        return 0.0
    return _clamp((value - low) / (high - low))


def compensation(
    country: str,
    level: str,
    salary_stated: float | None,
    comp: CompConfig,
    city: City | None,
    cfg: ScoringConfig,
) -> CompBreakdown:
    """Resolve gross, net, and purchasing-power-adjusted pay for a posting.

    Stated salary always wins. When neither a stated figure nor a table entry
    exists the result is 'unknown' with a zero contribution — the dashboard
    renders that as 'no data', never as a low salary.
    """
    country = (country or "").upper()

    if salary_stated:
        gross, source = float(salary_stated), "stated"
    else:
        gross = comp.salary_by_country.get(country, {}).get(level)
        source = "estimated" if gross is not None else "unknown"

    if gross is None:
        return CompBreakdown(None, None, None, "unknown", 0.0)

    tax = comp.effective_tax.get(country)
    if tax is None:
        return CompBreakdown(gross, None, None, source, 0.0)
    net = gross * (1.0 - tax)

    pli = comp.pli.get(country, 1.0)
    purchasing_power = net / pli if pli else net
    normalised = _clamp(purchasing_power / comp.reference_purchasing_power)

    return CompBreakdown(gross, net, purchasing_power, source, normalised)


def quality_of_life(city: City | None, cfg: ScoringConfig) -> float:
    """Weighted sun / nature / affordability score in [0, 1].

    An unknown city scores mid-range rather than zero, so a missing entry in
    cities.yaml does not silently bury an otherwise strong role.
    """
    if city is None:
        return UNKNOWN_CITY_QOL

    sun_low, sun_high = cfg.sunshine_range
    rent_low, rent_high = cfg.rent_range

    sunshine = _scale(city.sunshine_hours, sun_low, sun_high)
    nature = _clamp(city.nature / 10.0)
    rent = 1.0 - _scale(city.rent_index, rent_low, rent_high)

    weights = cfg.qol_weights
    total = weights.get("sunshine", 0) + weights.get("nature", 0) + weights.get("rent", 0)
    if total == 0:
        return UNKNOWN_CITY_QOL

    return (
        sunshine * weights.get("sunshine", 0)
        + nature * weights.get("nature", 0)
        + rent * weights.get("rent", 0)
    ) / total


def total_score(
    comp_norm: float, qol: float, role_fit: float, weights: dict[str, float]
) -> float:
    total = weights.get("comp", 0) + weights.get("qol", 0) + weights.get("fit", 0)
    if total == 0:
        return 0.0
    return (
        comp_norm * weights.get("comp", 0)
        + qol * weights.get("qol", 0)
        + role_fit * weights.get("fit", 0)
    ) / total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_score.py -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add src/jobhunt/score.py tests/test_score.py
git commit -m "feat: add compensation, quality-of-life and total scoring"
```

---

### Task 6: Snapshot fetching and the source protocol

**Files:**
- Create: `src/jobhunt/snapshot.py`
- Create: `src/jobhunt/sources/base.py`
- Create: `src/jobhunt/sources/manual.py`
- Test: `tests/test_snapshot.py`
- Test: `tests/test_sources_manual.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `html_to_text(html: str) -> str`
  - `fetch_text(url: str, client) -> str` — `client` is any object with `.get(url) -> response` having `.text` and `.raise_for_status()`. Injected, never constructed inside.
  - `save_snapshot(directory: Path, url: str, text: str) -> Path`
  - `RawPosting(source, url, title, employer_name, city, country, description, salary_stated, external_id)` in `sources/base.py`
  - `Source` protocol with `name: str` and `fetch(employer) -> list[RawPosting]`
  - `posting_from_url(url, text, title=None) -> RawPosting` in `sources/manual.py`

Later ATS modules implement `Source` and return `RawPosting` lists. Nothing else about them is assumed here.

- [ ] **Step 1: Write the failing test for snapshots**

Create `tests/test_snapshot.py`:

```python
import pytest

from jobhunt.snapshot import fetch_text, html_to_text, save_snapshot


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return self._response


def test_html_to_text_strips_tags():
    html = "<html><body><h1>GNSS Engineer</h1><p>Join us.</p></body></html>"
    assert html_to_text(html) == "GNSS Engineer\n\nJoin us."


def test_html_to_text_drops_script_and_style_content():
    html = "<body><script>var x=1;</script><style>p{color:red}</style><p>Real</p></body>"
    assert html_to_text(html) == "Real"


def test_html_to_text_unescapes_entities():
    assert html_to_text("<p>R&amp;S &mdash; Munich</p>") == "R&S — Munich"


def test_html_to_text_collapses_blank_runs():
    assert html_to_text("<p>a</p><p></p><p></p><p>b</p>") == "a\n\nb"


def test_fetch_text_uses_the_injected_client():
    client = FakeClient(FakeResponse("<p>Hello</p>"))
    assert fetch_text("https://example.com/job", client) == "Hello"
    assert client.calls == ["https://example.com/job"]


def test_fetch_text_raises_on_http_error():
    client = FakeClient(FakeResponse("nope", status=404))
    with pytest.raises(RuntimeError):
        fetch_text("https://example.com/missing", client)


def test_save_snapshot_writes_a_stable_path_per_url(tmp_path):
    first = save_snapshot(tmp_path, "https://example.com/job/1", "content one")
    second = save_snapshot(tmp_path, "https://example.com/job/1", "content two")
    assert first == second
    assert first.read_text(encoding="utf-8") == "content two"
    assert first.suffix == ".md"


def test_save_snapshot_separates_different_urls(tmp_path):
    a = save_snapshot(tmp_path, "https://example.com/a", "a")
    b = save_snapshot(tmp_path, "https://example.com/b", "b")
    assert a != b


def test_save_snapshot_creates_the_directory(tmp_path):
    target = tmp_path / "nested" / "postings"
    path = save_snapshot(target, "https://example.com/x", "x")
    assert path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_snapshot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobhunt.snapshot'`

- [ ] **Step 3: Write `src/jobhunt/snapshot.py`**

```python
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = "jobhunt/0.1 (personal job search tool; contact via the operator)"
IGNORED_TAGS = {"script", "style", "head", "noscript"}
BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in IGNORED_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in IGNORED_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Reduce an HTML page to readable plain text.

    Deliberately crude: snapshots exist so the posting text survives the
    posting being taken down, not to reproduce the page.
    """
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_text(url: str, client) -> str:
    """Fetch a URL through an injected client and return it as plain text."""
    response = client.get(url)
    response.raise_for_status()
    return html_to_text(response.text)


def save_snapshot(directory: Path, url: str, text: str) -> Path:
    """Write a snapshot to a path derived from the URL, so re-saving overwrites."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    path = directory / f"{digest}.md"
    path.write_text(text, encoding="utf-8")
    return path


def default_client():
    """Build the real HTTP client. Never called from tests."""
    import httpx

    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_snapshot.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Write the failing test for the manual source**

Create `tests/test_sources_manual.py`:

```python
from jobhunt.sources.manual import posting_from_url


def test_posting_from_url_infers_title_from_the_first_line():
    text = "Senior GNSS Engineer\n\nWe are hiring in Munich."
    p = posting_from_url("https://example.com/job/9", text)
    assert p.title == "Senior GNSS Engineer"
    assert p.description == text
    assert p.source == "manual"
    assert p.url == "https://example.com/job/9"


def test_explicit_title_overrides_the_inferred_one():
    p = posting_from_url("https://example.com/j", "Some Heading\n\nbody", title="PNT Engineer")
    assert p.title == "PNT Engineer"


def test_blank_text_yields_an_empty_title_not_a_crash():
    p = posting_from_url("https://example.com/j", "")
    assert p.title == ""
    assert p.description == ""


def test_overlong_first_line_is_not_used_as_a_title():
    text = "x" * 300 + "\n\nbody"
    p = posting_from_url("https://example.com/j", text)
    assert p.title == ""
```

- [ ] **Step 6: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_sources_manual.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobhunt.sources.manual'`

- [ ] **Step 7: Write `src/jobhunt/sources/base.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from jobhunt.models import Employer


@dataclass
class RawPosting:
    """A posting as it arrives from a source, before matching or scoring."""

    source: str
    url: str
    title: str = ""
    employer_name: str = ""
    city: str = ""
    country: str = ""
    description: str = ""
    salary_stated: float | None = None
    external_id: str = ""


class Source(Protocol):
    """Implemented by every discovery module, including future ATS pollers."""

    name: str

    def fetch(self, employer: Employer) -> list[RawPosting]:
        ...
```

- [ ] **Step 8: Write `src/jobhunt/sources/manual.py`**

```python
from __future__ import annotations

from jobhunt.sources.base import RawPosting

MAX_INFERRED_TITLE_LENGTH = 120


def posting_from_url(url: str, text: str, title: str | None = None) -> RawPosting:
    """Build a posting from a pasted URL and its fetched text.

    The title is taken from the first non-empty line unless it is implausibly
    long, in which case it is left blank for the operator to fill in.
    """
    text = text or ""
    inferred = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            if len(stripped) <= MAX_INFERRED_TITLE_LENGTH:
                inferred = stripped
            break

    return RawPosting(
        source="manual",
        url=url,
        title=title if title is not None else inferred,
        description=text,
    )
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sources_manual.py tests/test_snapshot.py -v`
Expected: PASS, 13 tests

- [ ] **Step 10: Commit**

```bash
git add src/jobhunt/snapshot.py src/jobhunt/sources tests/test_snapshot.py tests/test_sources_manual.py
git commit -m "feat: add snapshot fetching, source protocol and manual source"
```

---

### Task 7: Staleness and deadlines

**Files:**
- Create: `src/jobhunt/staleness.py`
- Create: `src/jobhunt/deadlines.py`
- Test: `tests/test_staleness.py`
- Test: `tests/test_deadlines.py`

**Interfaces:**
- Consumes: `Job`, `Event`, `Stage`, `Deadline` from `jobhunt.models`; `ScoringConfig` from `jobhunt.config`.
- Produces:
  - `DueItem(job_id, title, days_since, level, threshold)` and `due_jobs(jobs, events_by_job, cfg, today) -> list[DueItem]` in `staleness.py`. `level` is `"nudge"` or `"dormant"`. `events_by_job` is `dict[int, list[Event]]`.
  - `UpcomingDeadline(name, employer, closes, days_left, url, state)` and `upcoming(deadlines, today) -> list[UpcomingDeadline]` in `deadlines.py`. `state` is `"open"`, `"opening_soon"`, or `"closing_soon"`.

Staleness uses the `phd` threshold pair when a job's tags contain `phd`, otherwise `applied`. Only jobs in the `applied` stage are considered.

- [ ] **Step 1: Write the failing test for staleness**

Create `tests/test_staleness.py`:

```python
from datetime import date

import pytest

from jobhunt.config import RoleFamily, ScoringConfig
from jobhunt.models import Event, EventKind, Job, Stage
from jobhunt.staleness import due_jobs


@pytest.fixture
def cfg():
    return ScoringConfig(
        weights={"comp": 1, "qol": 1, "fit": 1}, qol_weights={},
        role_families=[RoleFamily("gnss", 1.0, ["gnss"])],
        staleness={"applied": [14, 30], "phd": [21, 45]},
    )


def applied_job(job_id, tags=None):
    return Job(id=job_id, employer_id=1, title=f"Job {job_id}",
               url=f"https://x/{job_id}", stage=Stage.APPLIED, tags=tags or [])


def event_on(job_id, day):
    return Event(job_id=job_id, kind=EventKind.STAGE, text="stage -> applied",
                 ts=f"{day}T00:00:00Z")


def test_recent_application_is_not_due(cfg):
    jobs = [applied_job(1)]
    events = {1: [event_on(1, "2026-07-20")]}
    assert due_jobs(jobs, events, cfg, date(2026, 7, 26)) == []


def test_application_past_the_first_threshold_is_a_nudge(cfg):
    jobs = [applied_job(1)]
    events = {1: [event_on(1, "2026-07-01")]}
    due = due_jobs(jobs, events, cfg, date(2026, 7, 26))
    assert len(due) == 1
    assert due[0].level == "nudge"
    assert due[0].days_since == 25


def test_application_past_the_second_threshold_is_dormant(cfg):
    jobs = [applied_job(1)]
    events = {1: [event_on(1, "2026-06-01")]}
    due = due_jobs(jobs, events, cfg, date(2026, 7, 26))
    assert due[0].level == "dormant"


def test_phd_tagged_jobs_use_the_slower_thresholds(cfg):
    jobs = [applied_job(1, tags=["phd"])]
    events = {1: [event_on(1, "2026-07-08")]}  # 18 days
    assert due_jobs(jobs, events, cfg, date(2026, 7, 26)) == []

    events = {1: [event_on(1, "2026-07-01")]}  # 25 days
    assert due_jobs(jobs, events, cfg, date(2026, 7, 26))[0].level == "nudge"


def test_the_most_recent_event_resets_the_clock(cfg):
    jobs = [applied_job(1)]
    events = {1: [event_on(1, "2026-06-01"),
                  Event(job_id=1, kind=EventKind.NOTE, text="they replied",
                        ts="2026-07-24T00:00:00Z")]}
    assert due_jobs(jobs, events, cfg, date(2026, 7, 26)) == []


def test_jobs_not_in_applied_stage_are_ignored(cfg):
    job = applied_job(1)
    job.stage = Stage.INTERVIEW
    assert due_jobs([job], {1: [event_on(1, "2026-01-01")]}, cfg, date(2026, 7, 26)) == []


def test_jobs_with_no_events_are_ignored(cfg):
    assert due_jobs([applied_job(1)], {}, cfg, date(2026, 7, 26)) == []


def test_results_are_sorted_most_stale_first(cfg):
    jobs = [applied_job(1), applied_job(2)]
    events = {1: [event_on(1, "2026-07-05")], 2: [event_on(2, "2026-06-01")]}
    due = due_jobs(jobs, events, cfg, date(2026, 7, 26))
    assert [d.job_id for d in due] == [2, 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_staleness.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobhunt.staleness'`

- [ ] **Step 3: Write `src/jobhunt/staleness.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from jobhunt.config import ScoringConfig
from jobhunt.models import Event, Job, Stage

DEFAULT_THRESHOLDS = [14, 30]


@dataclass
class DueItem:
    job_id: int
    title: str
    days_since: int
    level: str  # "nudge" | "dormant"
    threshold: int


def _last_event_date(events: list[Event]) -> date | None:
    stamps = [e.ts for e in events if e.ts]
    if not stamps:
        return None
    return date.fromisoformat(max(stamps)[:10])


def due_jobs(
    jobs: list[Job],
    events_by_job: dict[int, list[Event]],
    cfg: ScoringConfig,
    today: date,
) -> list[DueItem]:
    """Jobs sitting in 'applied' with no activity past their threshold.

    PhD-tagged jobs use the slower 'phd' thresholds, since research groups
    reply on a different clock to industry recruiters.
    """
    out: list[DueItem] = []
    for job in jobs:
        if job.stage is not Stage.APPLIED or job.id is None:
            continue
        last = _last_event_date(events_by_job.get(job.id, []))
        if last is None:
            continue

        key = "phd" if "phd" in job.tags else "applied"
        thresholds = cfg.staleness.get(key, DEFAULT_THRESHOLDS)
        nudge_at, dormant_at = thresholds[0], thresholds[-1]

        days = (today - last).days
        if days >= dormant_at:
            out.append(DueItem(job.id, job.title, days, "dormant", dormant_at))
        elif days >= nudge_at:
            out.append(DueItem(job.id, job.title, days, "nudge", nudge_at))

    out.sort(key=lambda d: d.days_since, reverse=True)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_staleness.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Write the failing test for deadlines**

Create `tests/test_deadlines.py`:

```python
from datetime import date

from jobhunt.deadlines import upcoming
from jobhunt.models import Deadline


def test_a_deadline_inside_its_lead_window_is_returned():
    d = Deadline(name="YGT", closes="2026-09-01", lead_days=60)
    result = upcoming([d], date(2026, 7, 26))
    assert len(result) == 1
    assert result[0].days_left == 37


def test_a_deadline_beyond_its_lead_window_is_hidden():
    d = Deadline(name="Far", closes="2027-06-01", lead_days=30)
    assert upcoming([d], date(2026, 7, 26)) == []


def test_a_closed_deadline_is_hidden():
    d = Deadline(name="Gone", closes="2026-07-01", lead_days=60)
    assert upcoming([d], date(2026, 7, 26)) == []


def test_not_yet_open_is_flagged_opening_soon():
    d = Deadline(name="YGT", opens="2026-09-01", closes="2026-11-15", lead_days=120)
    assert upcoming([d], date(2026, 7, 26))[0].state == "opening_soon"


def test_currently_open_is_flagged_open():
    d = Deadline(name="YGT", opens="2026-07-01", closes="2026-09-15", lead_days=120)
    assert upcoming([d], date(2026, 7, 26))[0].state == "open"


def test_open_and_closing_within_two_weeks_is_closing_soon():
    d = Deadline(name="Intake", opens="2026-06-01", closes="2026-08-05", lead_days=120)
    assert upcoming([d], date(2026, 7, 26))[0].state == "closing_soon"


def test_deadlines_without_a_close_date_are_hidden():
    assert upcoming([Deadline(name="Rolling")], date(2026, 7, 26)) == []


def test_results_are_sorted_by_urgency():
    a = Deadline(name="A", closes="2026-09-01", lead_days=90)
    b = Deadline(name="B", closes="2026-08-10", lead_days=90)
    assert [u.name for u in upcoming([a, b], date(2026, 7, 26))] == ["B", "A"]
```

- [ ] **Step 6: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_deadlines.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobhunt.deadlines'`

- [ ] **Step 7: Write `src/jobhunt/deadlines.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from jobhunt.models import Deadline

CLOSING_SOON_DAYS = 14


@dataclass
class UpcomingDeadline:
    name: str
    employer: str
    closes: str
    days_left: int
    url: str
    state: str  # "opening_soon" | "open" | "closing_soon"


def upcoming(deadlines: list[Deadline], today: date) -> list[UpcomingDeadline]:
    """Cycle-based opportunities worth acting on now.

    These are independent of live postings: a scheme that opens annually
    matters six weeks before it opens, when there are no postings to find.
    """
    out: list[UpcomingDeadline] = []
    for d in deadlines:
        if not d.closes:
            continue
        closes = date.fromisoformat(d.closes)
        days_left = (closes - today).days
        if days_left < 0 or days_left > d.lead_days:
            continue

        opens = date.fromisoformat(d.opens) if d.opens else None
        if opens and today < opens:
            state = "opening_soon"
        elif days_left <= CLOSING_SOON_DAYS:
            state = "closing_soon"
        else:
            state = "open"

        out.append(UpcomingDeadline(d.name, d.employer, d.closes, days_left,
                                    d.url, state))

    out.sort(key=lambda u: u.days_left)
    return out
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_deadlines.py -v`
Expected: PASS, 8 tests

- [ ] **Step 9: Commit**

```bash
git add src/jobhunt/staleness.py src/jobhunt/deadlines.py tests/test_staleness.py tests/test_deadlines.py
git commit -m "feat: add follow-up staleness and recurring deadline tracking"
```

---

### Task 8: Dashboard rendering

**Files:**
- Create: `src/jobhunt/report.py`
- Create: `src/jobhunt/templates/dashboard.html.j2`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `Store`, `ScoringConfig`, `CompConfig`, `City`, `Deadline`, `due_jobs`, `upcoming`.
- Produces: `build_context(store, deadlines, cfg, comp_cfg, cities, today) -> dict` and `render(context) -> str`.

Context keys: `generated_on`, `new_jobs`, `by_stage`, `due`, `deadlines`, `unwatched_employers`, `totals`. `by_stage` is an ordered `dict[str, list[JobView]]`. `JobView` carries the job plus its resolved employer name and a `comp_label` string that reads `"no data"` when the compensation source is unknown.

- [ ] **Step 1: Write the failing test**

Create `tests/test_report.py`:

```python
from datetime import date

import pytest

from jobhunt.config import City, CompConfig, RoleFamily, ScoringConfig
from jobhunt.models import Deadline, Employer, EventKind, Job, Stage
from jobhunt.report import build_context, render
from jobhunt.store import Store


@pytest.fixture
def cfg():
    return ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[RoleFamily("gnss", 1.0, ["gnss"])],
        staleness={"applied": [14, 30], "phd": [21, 45]},
        sunshine_range=[1500, 2500], rent_range=[500, 1500],
    )


@pytest.fixture
def comp_cfg():
    return CompConfig(
        salary_by_country={"DE": {"junior": 60000}},
        effective_tax={"DE": 0.40}, pli={"DE": 1.0},
        reference_purchasing_power=40000,
    )


@pytest.fixture
def cities():
    return {"munich": City("Munich", "DE", 1777, 9, 1400)}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "d.db")
    s.initialize()
    yield s
    s.close()


def test_context_groups_jobs_by_stage(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="Rohde & Schwarz", country="DE"))
    a = store.upsert_job(Job(employer_id=eid, title="GNSS Engineer",
                             url="https://x/a", country="DE", city="Munich",
                             first_seen="2026-07-25T00:00:00Z"))
    store.upsert_job(Job(employer_id=eid, title="Radar Engineer",
                         url="https://x/b", country="DE", city="Munich",
                         first_seen="2026-07-25T00:00:00Z"))
    store.set_stage(a, Stage.APPLIED, ts="2026-07-25T00:00:00Z")

    ctx = build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26))
    assert [j.job.title for j in ctx["by_stage"]["applied"]] == ["GNSS Engineer"]
    assert [j.job.title for j in ctx["by_stage"]["spotted"]] == ["Radar Engineer"]


def test_context_resolves_employer_names(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="Septentrio"))
    store.upsert_job(Job(employer_id=eid, title="GNSS Engineer", url="https://x/c"))
    ctx = build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26))
    assert ctx["by_stage"]["spotted"][0].employer_name == "Septentrio"


def test_unknown_compensation_reads_as_no_data(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="Unknown Co"))
    store.upsert_job(Job(employer_id=eid, title="GNSS Engineer",
                         url="https://x/d", country="JP"))
    ctx = build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26))
    assert ctx["by_stage"]["spotted"][0].comp_label == "no data"


def test_known_compensation_shows_figure_and_provenance(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="R&S"))
    store.upsert_job(Job(employer_id=eid, title="GNSS Engineer",
                         url="https://x/e", country="DE", city="Munich"))
    label = build_context(store, [], cfg, comp_cfg, cities,
                          date(2026, 7, 26))["by_stage"]["spotted"][0].comp_label
    assert "60" in label
    assert "estimated" in label


def test_dismissed_jobs_are_excluded(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="Noise"))
    jid = store.upsert_job(Job(employer_id=eid, title="Surveyor", url="https://x/f"))
    store.dismiss_job(jid, "not engineering", ts="2026-07-26T00:00:00Z")
    ctx = build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26))
    assert ctx["by_stage"]["spotted"] == []


def test_unwatched_employers_are_listed(store, cfg, comp_cfg, cities):
    store.upsert_employer(Employer(name="Small GmbH", ats="manual", poll_enabled=False))
    store.upsert_employer(Employer(name="Big SA", ats="greenhouse", poll_enabled=True))
    ctx = build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26))
    assert [e.name for e in ctx["unwatched_employers"]] == ["Small GmbH"]


def test_due_and_deadlines_are_included(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="ESA"))
    jid = store.upsert_job(Job(employer_id=eid, title="YGT", url="https://x/g"))
    store.set_stage(jid, Stage.APPLIED, ts="2026-06-01T00:00:00Z")
    deadlines = [Deadline(name="YGT round", closes="2026-08-15", lead_days=60)]

    ctx = build_context(store, deadlines, cfg, comp_cfg, cities, date(2026, 7, 26))
    assert ctx["due"][0].level == "dormant"
    assert ctx["deadlines"][0].name == "YGT round"


def test_render_produces_html_containing_the_jobs(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="Thales Alenia Space"))
    store.upsert_job(Job(employer_id=eid, title="Navigation Engineer",
                         url="https://x/h", country="FR", city="Toulouse"))
    html = render(build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26)))
    assert "<html" in html.lower()
    assert "Navigation Engineer" in html
    assert "Thales Alenia Space" in html


def test_render_escapes_employer_and_title_text(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="<script>alert(1)</script>"))
    store.upsert_job(Job(employer_id=eid, title="GNSS", url="https://x/i"))
    html = render(build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26)))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobhunt.report'`

- [ ] **Step 3: Write `src/jobhunt/report.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobhunt.config import City, CompConfig, ScoringConfig
from jobhunt.deadlines import upcoming
from jobhunt.models import Deadline, Job, Stage
from jobhunt.score import compensation
from jobhunt.staleness import due_jobs
from jobhunt.store import Store

TEMPLATE_DIR = Path(__file__).parent / "templates"
NEW_JOB_WINDOW_DAYS = 7
STAGE_ORDER = [
    Stage.SPOTTED, Stage.SHORTLISTED, Stage.APPLIED,
    Stage.SCREENING, Stage.INTERVIEW, Stage.OFFER,
]


@dataclass
class JobView:
    job: Job
    employer_name: str
    comp_label: str


def _comp_label(job: Job, comp_cfg: CompConfig, cities: dict[str, City],
                cfg: ScoringConfig) -> str:
    city = cities.get(job.city.lower()) if job.city else None
    breakdown = compensation(job.country, job.level, job.salary_stated,
                             comp_cfg, city, cfg)
    if breakdown.source == "unknown" or breakdown.gross is None:
        return "no data"
    gross = f"{breakdown.gross / 1000:.0f}k"
    if breakdown.purchasing_power is None:
        return f"{gross} gross ({breakdown.source})"
    pp = f"{breakdown.purchasing_power / 1000:.0f}k"
    return f"{gross} gross · {pp} PPP-net ({breakdown.source})"


def build_context(
    store: Store,
    deadlines: list[Deadline],
    cfg: ScoringConfig,
    comp_cfg: CompConfig,
    cities: dict[str, City],
    today: date,
) -> dict:
    employers = {e.id: e for e in store.list_employers()}
    jobs = store.list_jobs()

    def view(job: Job) -> JobView:
        employer = employers.get(job.employer_id)
        return JobView(
            job=job,
            employer_name=employer.name if employer else "unknown",
            comp_label=_comp_label(job, comp_cfg, cities, cfg),
        )

    by_stage: dict[str, list[JobView]] = {s.value: [] for s in STAGE_ORDER}
    for job in jobs:
        if job.stage.value in by_stage:
            by_stage[job.stage.value].append(view(job))

    cutoff = (today - timedelta(days=NEW_JOB_WINDOW_DAYS)).isoformat()
    new_jobs = [view(j) for j in jobs if (j.first_seen or "") >= cutoff]

    events_by_job = {j.id: store.list_events(j.id) for j in jobs if j.id is not None}

    return {
        "generated_on": today.isoformat(),
        "new_jobs": new_jobs,
        "by_stage": by_stage,
        "due": due_jobs(jobs, events_by_job, cfg, today),
        "deadlines": upcoming(deadlines, today),
        "unwatched_employers": [e for e in employers.values() if not e.poll_enabled],
        "totals": {
            "jobs": len(jobs),
            "employers": len(employers),
            "active": len([j for j in jobs if j.stage in STAGE_ORDER]),
        },
    }


def render(context: dict, template_dir: Path = TEMPLATE_DIR) -> str:
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "j2"]),
    )
    return env.get_template("dashboard.html.j2").render(**context)
```

- [ ] **Step 4: Write `src/jobhunt/templates/dashboard.html.j2`**

```jinja
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Job search — {{ generated_on }}</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 70rem;
           padding: 0 1rem; line-height: 1.5; }
    h1 { margin-bottom: 0; }
    .meta { opacity: 0.7; font-size: 0.9rem; }
    section { margin-top: 2.5rem; }
    table { border-collapse: collapse; width: 100%; }
    th, td { text-align: left; padding: 0.4rem 0.6rem;
             border-bottom: 1px solid rgba(128,128,128,0.3); }
    th { font-size: 0.8rem; text-transform: uppercase; opacity: 0.7; }
    .score { font-variant-numeric: tabular-nums; white-space: nowrap; }
    .breakdown { font-size: 0.8rem; opacity: 0.7; }
    .flag { font-size: 0.75rem; border: 1px solid currentColor; border-radius: 3px;
            padding: 0 0.3rem; opacity: 0.8; }
    .dormant { color: #b00; }
    .closing_soon { color: #b00; font-weight: 600; }
    .empty { opacity: 0.6; font-style: italic; }
  </style>
</head>
<body>
  <h1>Job search</h1>
  <p class="meta">
    Generated {{ generated_on }} ·
    {{ totals.jobs }} jobs · {{ totals.employers }} employers
  </p>

  <section>
    <h2>Upcoming deadlines</h2>
    {% if deadlines %}
    <table>
      <tr><th>Cycle</th><th>Employer</th><th>Closes</th><th>Days left</th><th>State</th></tr>
      {% for d in deadlines %}
      <tr>
        <td>{% if d.url %}<a href="{{ d.url }}">{{ d.name }}</a>{% else %}{{ d.name }}{% endif %}</td>
        <td>{{ d.employer }}</td>
        <td>{{ d.closes }}</td>
        <td class="score">{{ d.days_left }}</td>
        <td class="{{ d.state }}">{{ d.state.replace('_', ' ') }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}<p class="empty">Nothing in the lead window.</p>{% endif %}
  </section>

  <section>
    <h2>Follow-ups due</h2>
    {% if due %}
    <table>
      <tr><th>Job</th><th>Days since activity</th><th>Level</th></tr>
      {% for item in due %}
      <tr>
        <td>{{ item.title }}</td>
        <td class="score">{{ item.days_since }}</td>
        <td class="{{ item.level }}">{{ item.level }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}<p class="empty">Nothing overdue.</p>{% endif %}
  </section>

  <section>
    <h2>New in the last week</h2>
    {% if new_jobs %}
    <table>
      <tr><th>Title</th><th>Employer</th><th>Where</th><th>Compensation</th><th>Score</th></tr>
      {% for v in new_jobs %}
      <tr>
        <td><a href="{{ v.job.url }}">{{ v.job.title }}</a></td>
        <td>{{ v.employer_name }}</td>
        <td>{{ v.job.city }}{% if v.job.country %}, {{ v.job.country }}{% endif %}</td>
        <td>{{ v.comp_label }}</td>
        <td class="score">{{ '%.2f'|format(v.job.total_score) }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}<p class="empty">Nothing new.</p>{% endif %}
  </section>

  {% for stage, views in by_stage.items() %}
  <section>
    <h2>{{ stage|capitalize }} <span class="meta">({{ views|length }})</span></h2>
    {% if views %}
    <table>
      <tr><th>Title</th><th>Employer</th><th>Where</th><th>Compensation</th>
          <th>Score</th><th>Flags</th></tr>
      {% for v in views %}
      <tr>
        <td><a href="{{ v.job.url }}">{{ v.job.title }}</a></td>
        <td>{{ v.employer_name }}</td>
        <td>{{ v.job.city }}{% if v.job.country %}, {{ v.job.country }}{% endif %}</td>
        <td>{{ v.comp_label }}</td>
        <td class="score">
          {{ '%.2f'|format(v.job.total_score) }}
          <div class="breakdown">
            fit {{ '%.2f'|format(v.job.role_fit) }} ·
            comp {{ '%.2f'|format(v.job.comp_score) }} ·
            qol {{ '%.2f'|format(v.job.qol_score) }}
          </div>
        </td>
        <td>
          {% for f in v.job.language_flags %}<span class="flag">{{ f }}</span> {% endfor %}
          {% for t in v.job.tags %}<span class="flag">{{ t }}</span> {% endfor %}
        </td>
      </tr>
      {% endfor %}
    </table>
    {% else %}<p class="empty">Nothing here.</p>{% endif %}
  </section>
  {% endfor %}

  <section>
    <h2>Employers not polled — check these yourself</h2>
    {% if unwatched_employers %}
    <table>
      <tr><th>Employer</th><th>Careers page</th><th>Last checked</th></tr>
      {% for e in unwatched_employers %}
      <tr>
        <td>{{ e.name }}</td>
        <td>{% if e.careers_url %}<a href="{{ e.careers_url }}">{{ e.careers_url }}</a>{% endif %}</td>
        <td>{{ e.last_manual_check or 'never' }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}<p class="empty">Every employer is polled.</p>{% endif %}
  </section>
</body>
</html>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_report.py -v`
Expected: PASS, 9 tests

- [ ] **Step 6: Commit**

```bash
git add src/jobhunt/report.py src/jobhunt/templates tests/test_report.py
git commit -m "feat: add static HTML dashboard with score breakdowns"
```

---

### Task 9: CLI

**Files:**
- Create: `src/jobhunt/cli.py`
- Create: `config/employers.yaml`
- Create: `config/deadlines.yaml`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces: a Typer `app` exposing `add`, `list`, `show`, `stage`, `note`, `dismiss`, `reject`, `dismissed`, `due`, `report`, `sync-employers`.

Paths come from environment variables so tests can redirect them: `JOBHUNT_DB` (default `data/jobs.db`), `JOBHUNT_CONFIG` (default `config`), `JOBHUNT_POSTINGS` (default `data/postings`), `JOBHUNT_DASHBOARD` (default `dashboard.html`).

`jobs add` fetches the URL, snapshots it, runs `evaluate` and the scorers, and stores the result. It never rejects: a posting the matcher considers irrelevant is stored with a note in its reasons, because the operator chose to add it deliberately.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
import textwrap

import pytest
from typer.testing import CliRunner

from jobhunt import cli as cli_module
from jobhunt.cli import app
from jobhunt.models import Stage
from jobhunt.store import Store

runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "scoring.yaml").write_text(textwrap.dedent("""
        weights: {comp: 0.3, qol: 0.2, fit: 0.5}
        qol_weights: {sunshine: 0.5, nature: 0.3, rent: 0.2}
        sunshine_range: [1500, 2500]
        rent_range: [500, 1500]
        excluded_countries: [GB]
        known_languages: [en, fr]
        language_keywords: {de: ["fluent german"]}
        negative_keywords: ["land surveyor"]
        staleness: {applied: [14, 30], phd: [21, 45]}
        role_families:
          - {name: gnss, weight: 1.0, keywords: ["gnss", "galileo"]}
    """))
    (config / "cities.yaml").write_text(textwrap.dedent("""
        cities:
          - {name: Munich, country: DE, sunshine_hours: 1777, nature: 9, rent_index: 1400}
    """))
    (config / "comp.yaml").write_text(textwrap.dedent("""
        reference_purchasing_power: 40000
        effective_tax: {DE: 0.4}
        pli: {DE: 1.0}
        salary_by_country: {DE: {junior: 60000}}
    """))
    (config / "employers.yaml").write_text(textwrap.dedent("""
        employers:
          - {name: Rohde & Schwarz, country: DE, city: Munich, ats: manual,
             careers_url: "https://example.com/careers", tags: [gnss]}
    """))
    (config / "deadlines.yaml").write_text(textwrap.dedent("""
        deadlines:
          - {name: ESA YGT, employer: ESA, closes: 2026-08-15, lead_days: 60,
             url: "https://example.com/ygt"}
    """))

    monkeypatch.setenv("JOBHUNT_CONFIG", str(config))
    monkeypatch.setenv("JOBHUNT_DB", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("JOBHUNT_POSTINGS", str(tmp_path / "postings"))
    monkeypatch.setenv("JOBHUNT_DASHBOARD", str(tmp_path / "dashboard.html"))
    monkeypatch.setattr(
        cli_module, "_today", lambda: __import__("datetime").date(2026, 7, 26)
    )
    return tmp_path


class FakeResponse:
    text = "<h1>GNSS Engineer</h1><p>Galileo receiver work in Munich.</p>"

    def raise_for_status(self):
        return None


class FakeClient:
    def get(self, url):
        return FakeResponse()

    def close(self):
        return None


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr(cli_module, "_http_client", lambda: FakeClient())


def test_sync_employers_loads_the_yaml(env):
    result = runner.invoke(app, ["sync-employers"])
    assert result.exit_code == 0, result.output
    store = Store(env / "jobs.db")
    assert [e.name for e in store.list_employers()] == ["Rohde & Schwarz"]
    store.close()


def test_add_fetches_snapshots_and_scores(env, fake_http):
    runner.invoke(app, ["sync-employers"])
    result = runner.invoke(app, [
        "add", "https://example.com/job/1", "--employer", "Rohde & Schwarz",
        "--city", "Munich", "--country", "DE",
    ])
    assert result.exit_code == 0, result.output

    store = Store(env / "jobs.db")
    jobs = store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].title == "GNSS Engineer"
    assert jobs[0].role_fit > 0
    assert jobs[0].total_score > 0
    assert (env / "postings").exists()
    assert jobs[0].snapshot_path
    store.close()


def test_add_creates_an_unknown_employer_on_demand(env, fake_http):
    result = runner.invoke(app, [
        "add", "https://example.com/job/2", "--employer", "Some New GmbH",
    ])
    assert result.exit_code == 0, result.output
    store = Store(env / "jobs.db")
    assert store.find_employer_by_name("Some New GmbH") is not None
    store.close()


def test_add_stores_an_irrelevant_posting_with_a_warning(env, fake_http, monkeypatch):
    class Irrelevant(FakeResponse):
        text = "<h1>Accountant</h1><p>Bookkeeping.</p>"

    monkeypatch.setattr(
        cli_module, "_http_client",
        lambda: type("C", (), {"get": lambda self, url: Irrelevant(),
                               "close": lambda self: None})(),
    )
    result = runner.invoke(app, ["add", "https://example.com/job/3",
                                 "--employer", "X"])
    assert result.exit_code == 0
    assert "no role family" in result.output
    store = Store(env / "jobs.db")
    assert len(store.list_jobs()) == 1
    store.close()


def test_stage_transition_and_listing(env, fake_http):
    runner.invoke(app, ["add", "https://example.com/job/4", "--employer", "X"])
    store = Store(env / "jobs.db")
    job_id = store.list_jobs()[0].id
    store.close()

    assert runner.invoke(app, ["stage", str(job_id), "applied"]).exit_code == 0
    listing = runner.invoke(app, ["list", "--stage", "applied"])
    assert "GNSS Engineer" in listing.output

    store = Store(env / "jobs.db")
    assert store.get_job(job_id).stage is Stage.APPLIED
    store.close()


def test_invalid_stage_is_rejected(env, fake_http):
    runner.invoke(app, ["add", "https://example.com/job/5", "--employer", "X"])
    result = runner.invoke(app, ["stage", "1", "nonsense"])
    assert result.exit_code != 0
    assert "nonsense" in result.output


def test_dismiss_hides_from_list_and_shows_in_review(env, fake_http):
    runner.invoke(app, ["add", "https://example.com/job/6", "--employer", "X"])
    runner.invoke(app, ["dismiss", "1", "--reason", "wrong domain"])

    assert "GNSS Engineer" not in runner.invoke(app, ["list"]).output
    review = runner.invoke(app, ["dismissed", "--review"])
    assert "wrong domain" in review.output


def test_reject_sets_the_rejected_stage(env, fake_http):
    runner.invoke(app, ["add", "https://example.com/job/7", "--employer", "X"])
    runner.invoke(app, ["reject", "1"])
    store = Store(env / "jobs.db")
    assert store.get_job(1).stage is Stage.REJECTED
    store.close()


def test_note_is_recorded_as_an_event(env, fake_http):
    runner.invoke(app, ["add", "https://example.com/job/8", "--employer", "X"])
    runner.invoke(app, ["note", "1", "spoke to the team lead"])
    show = runner.invoke(app, ["show", "1"])
    assert "spoke to the team lead" in show.output


def test_due_lists_deadlines(env):
    runner.invoke(app, ["sync-employers"])
    result = runner.invoke(app, ["due"])
    assert "ESA YGT" in result.output


def test_report_writes_the_dashboard(env, fake_http):
    runner.invoke(app, ["sync-employers"])
    runner.invoke(app, ["add", "https://example.com/job/9", "--employer",
                        "Rohde & Schwarz", "--city", "Munich", "--country", "DE"])
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0, result.output
    dashboard = env / "dashboard.html"
    assert dashboard.exists()
    assert "GNSS Engineer" in dashboard.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobhunt.cli'`

- [ ] **Step 3: Write `src/jobhunt/cli.py`**

```python
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from jobhunt.config import (
    load_cities, load_comp, load_deadlines, load_employers, load_scoring,
)
from jobhunt.match import evaluate
from jobhunt.models import Employer, EventKind, Job, Stage
from jobhunt.report import build_context, render
from jobhunt.score import compensation, quality_of_life, total_score
from jobhunt.snapshot import default_client, fetch_text, save_snapshot
from jobhunt.sources.manual import posting_from_url
from jobhunt.store import Store

app = typer.Typer(help="Track GNSS/PNT/radar job opportunities.")


def _today() -> date:
    """Indirection so tests can pin the date."""
    return date.today()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_client():
    """Indirection so tests can inject a fake client."""
    return default_client()


def _config_dir() -> Path:
    return Path(os.environ.get("JOBHUNT_CONFIG", "config"))


def _open_store() -> Store:
    store = Store(Path(os.environ.get("JOBHUNT_DB", "data/jobs.db")))
    store.initialize()
    return store


def _postings_dir() -> Path:
    return Path(os.environ.get("JOBHUNT_POSTINGS", "data/postings"))


def _dashboard_path() -> Path:
    return Path(os.environ.get("JOBHUNT_DASHBOARD", "dashboard.html"))


def _load_all():
    cd = _config_dir()
    return (
        load_scoring(cd / "scoring.yaml"),
        load_comp(cd / "comp.yaml"),
        load_cities(cd / "cities.yaml"),
    )


@app.command("sync-employers")
def sync_employers() -> None:
    """Load config/employers.yaml into the database."""
    store = _open_store()
    employers = load_employers(_config_dir() / "employers.yaml")
    for employer in employers:
        store.upsert_employer(employer)
    typer.echo(f"synced {len(employers)} employers")
    store.close()


@app.command()
def add(
    url: str,
    employer: str = typer.Option(..., "--employer", "-e"),
    city: str = typer.Option("", "--city"),
    country: str = typer.Option("", "--country"),
    level: str = typer.Option("junior", "--level"),
    salary: Optional[float] = typer.Option(None, "--salary"),
    tags: str = typer.Option("", "--tags", help="comma-separated"),
    title: Optional[str] = typer.Option(None, "--title"),
) -> None:
    """Fetch a posting URL, snapshot it, score it, and store it."""
    cfg, comp_cfg, cities = _load_all()
    store = _open_store()

    known = store.find_employer_by_name(employer)
    if known is None:
        employer_id = store.upsert_employer(
            Employer(name=employer, country=country, city=city)
        )
        typer.echo(f"created employer: {employer}")
    else:
        employer_id = known.id
        country = country or known.country
        city = city or known.city

    client = _http_client()
    try:
        text = fetch_text(url, client)
    finally:
        close = getattr(client, "close", None)
        if close:
            close()

    posting = posting_from_url(url, text, title=title)
    snapshot_path = save_snapshot(_postings_dir(), url, text)

    match = evaluate(posting.title, posting.description, country, cfg)
    for reason in match.reasons:
        typer.echo(f"warning: {reason}")

    city_entry = cities.get(city.lower()) if city else None
    breakdown = compensation(country, level, salary, comp_cfg, city_entry, cfg)
    qol = quality_of_life(city_entry, cfg)
    total = total_score(breakdown.normalised, qol, match.role_fit, cfg.weights)

    now = _now()
    job_id = store.upsert_job(Job(
        employer_id=employer_id, title=posting.title, url=url, city=city,
        country=country, source="manual", snapshot_path=str(snapshot_path),
        first_seen=now, last_seen=now, role_fit=match.role_fit,
        comp_score=breakdown.normalised, qol_score=qol, total_score=total,
        salary_stated=salary, level=level,
        language_flags=match.language_flags,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
    ))
    typer.echo(
        f"[{job_id}] {posting.title} — score {total:.2f} "
        f"(fit {match.role_fit:.2f}, comp {breakdown.normalised:.2f}, qol {qol:.2f})"
    )
    store.close()


@app.command("list")
def list_jobs(
    stage: Optional[str] = typer.Option(None, "--stage"),
    all_jobs: bool = typer.Option(False, "--all"),
) -> None:
    """List tracked jobs, highest score first."""
    store = _open_store()
    stage_enum = _parse_stage(stage) if stage else None
    jobs = store.list_jobs(stage=stage_enum, include_dismissed=all_jobs)
    employers = {e.id: e.name for e in store.list_employers()}
    for job in jobs:
        flags = " ".join(f"[{f}]" for f in job.language_flags)
        typer.echo(
            f"{job.id:>4}  {job.total_score:.2f}  {job.stage.value:<11} "
            f"{job.title}  —  {employers.get(job.employer_id, '?')}  {flags}"
        )
    if not jobs:
        typer.echo("nothing to show")
    store.close()


@app.command()
def show(job_id: int) -> None:
    """Show a job's detail, scores, and event history."""
    store = _open_store()
    job = store.get_job(job_id)
    if job is None:
        typer.echo(f"no job with id {job_id}")
        raise typer.Exit(code=1)
    employer = store.get_employer(job.employer_id)

    typer.echo(f"{job.title}  [{job.stage.value}]")
    typer.echo(f"employer:  {employer.name if employer else '?'}")
    typer.echo(f"where:     {job.city} {job.country}")
    typer.echo(f"url:       {job.url}")
    typer.echo(f"snapshot:  {job.snapshot_path}")
    typer.echo(
        f"score:     {job.total_score:.2f} "
        f"(fit {job.role_fit:.2f}, comp {job.comp_score:.2f}, qol {job.qol_score:.2f})"
    )
    if job.language_flags:
        typer.echo(f"languages: {', '.join(job.language_flags)}")
    if job.dismissed:
        typer.echo(f"dismissed: {job.dismiss_reason}")
    typer.echo("events:")
    for event in store.list_events(job_id):
        typer.echo(f"  {event.ts[:10]}  {event.kind.value:<8} {event.text}")
    store.close()


def _parse_stage(value: str) -> Stage:
    try:
        return Stage(value)
    except ValueError:
        valid = ", ".join(s.value for s in Stage)
        typer.echo(f"invalid stage '{value}'. valid stages: {valid}")
        raise typer.Exit(code=2)


@app.command()
def stage(job_id: int, new_stage: str) -> None:
    """Move a job to a new pipeline stage."""
    store = _open_store()
    store.set_stage(job_id, _parse_stage(new_stage), ts=_now())
    typer.echo(f"[{job_id}] -> {new_stage}")
    store.close()


@app.command()
def note(job_id: int, text: str) -> None:
    """Attach a note to a job."""
    store = _open_store()
    store.add_event(job_id, EventKind.NOTE, text, ts=_now())
    typer.echo(f"[{job_id}] noted")
    store.close()


@app.command()
def dismiss(job_id: int, reason: str = typer.Option(..., "--reason", "-r")) -> None:
    """Permanently hide a job. Your judgment overrides the score."""
    store = _open_store()
    store.dismiss_job(job_id, reason, ts=_now())
    typer.echo(f"[{job_id}] dismissed: {reason}")
    store.close()


@app.command()
def reject(job_id: int) -> None:
    """Record that the employer rejected you."""
    store = _open_store()
    store.set_stage(job_id, Stage.REJECTED, ts=_now())
    typer.echo(f"[{job_id}] -> rejected")
    store.close()


@app.command()
def dismissed(review: bool = typer.Option(False, "--review")) -> None:
    """List dismissed jobs and their reasons, to inform negative keywords."""
    store = _open_store()
    jobs = [j for j in store.list_jobs(include_dismissed=True) if j.dismissed]
    for job in jobs:
        typer.echo(f"{job.id:>4}  {job.title}  —  {job.dismiss_reason}")
    if review and jobs:
        typer.echo(
            "\nRecurring reasons are candidates for config/scoring.yaml "
            "negative_keywords."
        )
    if not jobs:
        typer.echo("nothing dismissed")
    store.close()


@app.command()
def due() -> None:
    """Show follow-ups due and upcoming application cycles."""
    from jobhunt.deadlines import upcoming
    from jobhunt.staleness import due_jobs

    cfg, _, _ = _load_all()
    store = _open_store()
    today = _today()

    jobs = store.list_jobs()
    events = {j.id: store.list_events(j.id) for j in jobs if j.id is not None}
    items = due_jobs(jobs, events, cfg, today)

    typer.echo("follow-ups due:")
    for item in items or []:
        typer.echo(f"  [{item.job_id}] {item.title} — {item.days_since}d ({item.level})")
    if not items:
        typer.echo("  none")

    typer.echo("\nupcoming cycles:")
    cycles = upcoming(load_deadlines(_config_dir() / "deadlines.yaml"), today)
    for cycle in cycles:
        typer.echo(f"  {cycle.name} ({cycle.employer}) — {cycle.days_left}d, {cycle.state}")
    if not cycles:
        typer.echo("  none")
    store.close()


@app.command()
def report() -> None:
    """Regenerate the static HTML dashboard."""
    cfg, comp_cfg, cities = _load_all()
    store = _open_store()
    deadlines = load_deadlines(_config_dir() / "deadlines.yaml")
    context = build_context(store, deadlines, cfg, comp_cfg, cities, _today())
    path = _dashboard_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(context), encoding="utf-8")
    typer.echo(f"wrote {path}")
    store.close()


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Create `config/employers.yaml`**

This is a **seed list, not a verified one**. Every entry is `ats: manual` until the
discovery pass in Plan 2 establishes which platform each employer actually uses.
Cities and countries are the main sites, and several of these employers have more.

```yaml
employers:
  # --- industry: GNSS / PNT ---
  - {name: u-blox, country: CH, city: Thalwil, ats: manual, tags: [gnss, industry],
     careers_url: "https://www.u-blox.com/en/careers"}
  - {name: Septentrio, country: BE, city: Leuven, ats: manual, tags: [gnss, industry],
     careers_url: "https://www.septentrio.com/en/careers"}
  - {name: Rohde & Schwarz, country: DE, city: Munich, ats: manual, tags: [gnss, radar, industry],
     careers_url: "https://www.rohde-schwarz.com/careers"}
  - {name: Trimble, country: DE, city: Raunheim, ats: manual, tags: [gnss, industry],
     careers_url: "https://careers.trimble.com/"}
  - {name: Hexagon, country: CH, city: Heerbrugg, ats: manual, tags: [gnss, industry],
     careers_url: "https://hexagon.com/company/careers"}
  - {name: IFEN, country: DE, city: Poing, ats: manual, tags: [gnss, sme],
     careers_url: "https://www.ifen.com/company/careers/"}
  - {name: Qascom, country: IT, city: Bassano del Grappa, ats: manual, tags: [gnss, pnt, sme],
     careers_url: "https://www.qascom.it/careers/"}
  - {name: NavCert, country: DE, city: Braunschweig, ats: manual, tags: [gnss, sme],
     careers_url: "https://www.navcert.de/"}

  # --- industry: primes and space ---
  - {name: Airbus Defence and Space, country: DE, city: Munich, ats: manual, tags: [space, defense],
     careers_url: "https://www.airbus.com/en/careers"}
  - {name: Thales Alenia Space, country: FR, city: Toulouse, ats: manual, tags: [space, defense],
     careers_url: "https://www.thalesaleniaspace.com/en/careers"}
  - {name: Thales, country: FR, city: Toulouse, ats: manual, tags: [radar, defense],
     careers_url: "https://www.thalesgroup.com/en/careers"}
  - {name: Safran Data Systems, country: FR, city: Toulouse, ats: manual, tags: [pnt, industry],
     careers_url: "https://www.safran-group.com/careers"}
  - {name: GMV, country: ES, city: Madrid, ats: manual, tags: [gnss, space],
     careers_url: "https://www.gmv.com/en/talent"}
  - {name: Telespazio, country: IT, city: Rome, ats: manual, tags: [gnss, space],
     careers_url: "https://www.telespazio.com/en/careers"}
  - {name: Leonardo, country: IT, city: Rome, ats: manual, tags: [radar, defense],
     careers_url: "https://www.leonardo.com/en/careers"}

  # --- agencies and research ---
  - {name: ESA, country: NL, city: Noordwijk, ats: manual, tags: [space, agency, ygt],
     careers_url: "https://jobs.esa.int/"}
  - {name: DLR, country: DE, city: Oberpfaffenhofen, ats: manual, tags: [gnss, research, phd],
     careers_url: "https://www.dlr.de/en/careers"}
  - {name: Fraunhofer IIS, country: DE, city: Nuremberg, ats: manual, tags: [gnss, research, phd],
     careers_url: "https://www.iis.fraunhofer.de/en/jobs.html"}
  - {name: CNES, country: FR, city: Toulouse, ats: manual, tags: [space, agency],
     careers_url: "https://recrutement.cnes.fr/"}
  - {name: ONERA, country: FR, city: Toulouse, ats: manual, tags: [radar, research, phd],
     careers_url: "https://www.onera.fr/en/jobs"}

  # --- universities ---
  - {name: Universitaet der Bundeswehr Muenchen, country: DE, city: Munich,
     ats: manual, tags: [gnss, academic, phd],
     careers_url: "https://www.unibw.de/home/stellenangebote",
     notes: "Institute of Space Technology and Space Applications — strong GNSS group"}
  - {name: ISAE-SUPAERO, country: FR, city: Toulouse, ats: manual, tags: [gnss, academic, phd],
     careers_url: "https://www.isae-supaero.fr/en/"}
  - {name: TU Delft, country: NL, city: Delft, ats: manual, tags: [gnss, academic, phd],
     careers_url: "https://www.tudelft.nl/over-tu-delft/werken-bij-tu-delft"}
  - {name: KU Leuven, country: BE, city: Leuven, ats: manual, tags: [academic, phd],
     careers_url: "https://www.kuleuven.be/personeel/jobsite/vacatures"}
  - {name: Politecnico di Torino, country: IT, city: Turin, ats: manual, tags: [gnss, academic, phd],
     careers_url: "https://www.polito.it/en/work-with-us"}
```

- [ ] **Step 5: Create `config/deadlines.yaml`**

**Every date below is a placeholder marked `VERIFY`.** They encode the approximate
annual pattern, not confirmed dates. Task 10 replaces them with checked ones.

```yaml
deadlines:
  - name: ESA Young Graduate Trainee
    employer: ESA
    opens: 2026-10-01
    closes: 2026-11-30
    url: "https://jobs.esa.int/"
    lead_days: 90
    notes: "VERIFY — YGT typically opens in autumn for the following autumn intake"

  - name: Airbus graduate programme intake
    employer: Airbus Defence and Space
    opens: 2026-09-01
    closes: 2026-12-31
    url: "https://www.airbus.com/en/careers"
    lead_days: 60
    notes: "VERIFY — confirm intake windows on the careers site"

  - name: Thales graduate programme intake
    employer: Thales
    opens: 2026-09-01
    closes: 2026-12-31
    url: "https://www.thalesgroup.com/en/careers"
    lead_days: 60
    notes: "VERIFY — confirm intake windows on the careers site"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS, 11 tests

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: PASS, 90 tests

- [ ] **Step 8: Commit**

```bash
git add src/jobhunt/cli.py config/employers.yaml config/deadlines.yaml tests/test_cli.py
git commit -m "feat: add CLI with add, track, dismiss, due and report commands"
```

---

### Task 10: Profile scaffolding and README

**Files:**
- Create: `profile/evidence.md`
- Create: `profile/positioning.md`
- Create: `profile/answers.md`
- Create: `profile/cv/.gitkeep`
- Create: `README.md`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: no code. These are the user-authored files the tailoring conversations read.

No tests: this task creates prose templates and documentation, which the test suite has nothing to assert about.

- [ ] **Step 1: Create `profile/evidence.md`**

```markdown
# Evidence

Atomic, factual claims about work actually done. Not CV bullets — raw material.
Include numbers, tools, datasets, and results wherever they exist. Everything in a
CV or cover letter should be traceable to a line in this file.

## Telespazio internship

- Role, dates, team:
- Problem worked on:
- Methods and tools used:
- Result, with numbers:
- What went wrong and what you learned from it:

## Thesis

- Title:
- Question it answers:
- Methods:
- Results, with numbers:
- Validation approach and datasets:

## Projects

- Project:
  - What it does:
  - Your contribution specifically:
  - Result:

## Skills, with evidence

For each, name where you actually used it — a skill with no evidence line is a
skill you cannot defend in an interview.

- Languages (C++, Python, MATLAB, …):
- GNSS-specific (receiver architecture, RTK/PPP, RINEX, signal simulation, …):
- Estimation (Kalman variants, factor graphs, …):
- Tools (Git, Linux, GNSS-SDR, gnuradio, …):

## Coursework worth naming

## Publications, posters, talks

## Languages

- English:
- French:
- Spanish:
- Portuguese:
```

- [ ] **Step 2: Create `profile/positioning.md`**

```markdown
# Positioning angles

Two to four angles you can credibly lead with. Same person, different emphasis.
Each maps to a role family in config/scoring.yaml and to a base CV in profile/cv/.

## Angle 1: <name>

- Role families it targets:
- One-sentence pitch:
- Three strongest pieces of evidence (link to evidence.md sections):
- Base CV: profile/cv/<file>
- Employers this fits:

## Angle 2: <name>

(same structure)

## Notes

A fresh graduate reads as generic when every application makes the same broad
claim. Pick the angle per employer and commit to it.
```

- [ ] **Step 3: Create `profile/answers.md`**

```markdown
# Reusable answers

Written once, adapted per application. Keep them specific enough to be useful and
general enough to reuse.

## Why this domain (GNSS/PNT)?

## Why this company?

Per-employer notes go here as you research them.

- ESA:
- Airbus DS:
- Thales Alenia Space:

## Availability

- Graduation date:
- Earliest start:

## Salary expectation

- Target range by country (see config/comp.yaml for the reference figures):
- How to answer when asked first:

## Relocation

## Why a PhD / why not a PhD

Both versions, since you are applying to both.
```

- [ ] **Step 4: Create `profile/cv/.gitkeep` and update `.gitignore`**

```bash
mkdir -p profile/cv && touch profile/cv/.gitkeep
```

Append to `.gitignore`:

```
data/postings/
```

Snapshots are fetched content, not authored work — they stay local. `data/jobs.db`
is already ignored.

- [ ] **Step 5: Create `README.md`**

```markdown
# Job search tracker

Tracks GNSS/PNT/radar engineering opportunities across Europe: a curated employer
watchlist, a pipeline with follow-up nudges, configurable scoring, and a static
dashboard.

## Setup

    python -m venv .venv
    .venv/bin/pip install -e ".[dev]"
    .venv/bin/jobs sync-employers

## Daily use

    jobs add <url> --employer "Septentrio" --city Leuven --country BE
    jobs list
    jobs stage 12 applied
    jobs note 12 "recruiter call booked for Tuesday"
    jobs due
    jobs report && xdg-open dashboard.html

## Dismissing

    jobs dismiss 14 --reason "surveying, not engineering"   # you reject the job
    jobs reject 12                                          # they reject you
    jobs dismissed --review                                 # tune negative keywords

## Configuration

Everything tunable lives in `config/`:

| File | What it controls |
|---|---|
| `scoring.yaml` | Weights, role families, negative keywords, staleness thresholds |
| `cities.yaml` | Sunshine hours, nature score, rent — quality-of-life inputs |
| `comp.yaml` | Salary tables, effective tax rates, price level indices |
| `employers.yaml` | The watchlist |
| `deadlines.yaml` | Recurring application cycles |

The seed values in `cities.yaml`, `comp.yaml`, and `deadlines.yaml` are estimates.
Correct them as real numbers emerge — they are inputs you own, not facts the tool
asserts. Entries in `deadlines.yaml` marked `VERIFY` have not been checked against
the source.

## Scoring

`total = w_comp · comp + w_qol · qol + w_fit · role_fit`, all weights in
`scoring.yaml`. Compensation prefers a stated salary, falls back to the country
table, and reports `no data` rather than guessing. Score sorts; it never rejects.

## Profile

`profile/` holds the content the tailoring work draws on — `evidence.md`,
`positioning.md`, `answers.md`, and base CVs. Nothing generates documents
unattended.

## Not yet built

ATS polling (`jobs poll`) and the per-platform source modules. The `Source`
protocol in `src/jobhunt/sources/base.py` is the interface they implement.
```

- [ ] **Step 6: Verify the suite still passes**

Run: `.venv/bin/pytest -v`
Expected: PASS, 90 tests

- [ ] **Step 7: Commit**

```bash
git add profile README.md .gitignore
git commit -m "docs: add profile templates and README"
```

---

## Verification

After Task 10, confirm end to end:

```bash
.venv/bin/pytest -v                    # 90 tests pass
.venv/bin/jobs sync-employers          # 25 employers
.venv/bin/jobs due                     # deadlines listed
.venv/bin/jobs report                  # dashboard.html written
```

## What this plan deliberately leaves for the operator

- **`config/deadlines.yaml` dates are unverified.** Every entry carries a `VERIFY`
  note. Checking them against the source sites is manual work and is arguably more
  urgent than the code, since a missed annual cycle costs a year.
- **`config/cities.yaml` and `config/comp.yaml` are seeded with estimates.**
  They are honest starting points, not researched figures.
- **`profile/evidence.md` is a template.** It cannot be filled in by anyone else,
  and everything downstream depends on it.

## Follow-up plan

`docs/superpowers/plans/<date>-job-tracker-ats-polling.md`, to be written after the
ATS discovery pass establishes which platform each employer uses. It adds the five
source modules and the `jobs poll` command against the `Source` protocol defined in
Task 6.
