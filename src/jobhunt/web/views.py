from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobhunt.config import load_cities, load_comp, load_deadlines, load_scoring
from jobhunt.deadlines import upcoming
from jobhunt.models import Job, Stage
from jobhunt.score import compensation
from jobhunt.staleness import due_jobs
from jobhunt.store import Store
from jobhunt.web.deps import Deps

# (slug, label, blurb) — the blurb is what the Overview tab explains.
TABS = [
    ("overview", "Overview",
     "What is in each tab, what is due, and what closes soon."),
    ("search", "Job Search",
     "Newly spotted jobs and the controls to find or re-check more."),
    ("interested", "Interested",
     "Shortlisted jobs, ranked by the priority you set."),
    ("applied", "Applied",
     "Everything you have sent, grouped by how far it has got."),
    ("archive", "Archive",
     "Jobs you ruled out or that ruled you out, with the reason."),
    ("deadlines", "Deadlines",
     "Recurring application cycles that open and close on a fixed calendar."),
]

APPLIED_STAGES = [Stage.APPLIED, Stage.SCREENING, Stage.INTERVIEW, Stage.OFFER]
ARCHIVE_STAGES = [Stage.REJECTED, Stage.WITHDRAWN, Stage.EXPIRED]


@dataclass
class JobRow:
    job: Job
    employer_name: str
    comp_label: str
    stale_days: int | None = None


def _load_config(deps: Deps):
    cd = deps.config_dir
    return (
        load_scoring(cd / "scoring.yaml"),
        load_comp(cd / "comp.yaml"),
        load_cities(cd / "cities.yaml"),
    )


def _comp_label(job: Job, comp_cfg, cities, cfg) -> str:
    city = cities.get(job.city.lower()) if job.city else None
    b = compensation(job.country, job.level, job.salary_stated, comp_cfg, city, cfg)
    if b.source == "unknown" or b.gross is None:
        return "no data"
    gross = f"{b.gross / 1000:.0f}k"
    if b.purchasing_power is None:
        return f"{gross} gross ({b.source})"
    return f"{gross} gross · {b.purchasing_power / 1000:.0f}k PPP-net ({b.source})"


def _rows(store: Store, jobs: list[Job], deps: Deps) -> list[JobRow]:
    cfg, comp_cfg, cities = _load_config(deps)
    employers = {e.id: e.name for e in store.list_employers()}
    return [
        JobRow(
            job=job,
            employer_name=employers.get(job.employer_id, "unknown"),
            comp_label=_comp_label(job, comp_cfg, cities, cfg),
        )
        for job in jobs
    ]


def _days_since_last_event(store: Store, job: Job, today: date) -> int | None:
    if job.id is None:
        return None
    stamps = [e.ts for e in store.list_events(job.id) if e.ts]
    if not stamps:
        return None
    return (today - date.fromisoformat(max(stamps)[:10])).days


def overview_context(store: Store, deps: Deps) -> dict:
    cfg, _, _ = _load_config(deps)
    today = deps.today()
    jobs = store.list_jobs()
    archived = [j for j in store.list_jobs(include_dismissed=True) if j.dismissed]

    events = {j.id: store.list_events(j.id) for j in jobs if j.id is not None}
    return {
        "tabs": TABS,
        "active": "overview",
        "counts": {
            "search": len([j for j in jobs if j.stage is Stage.SPOTTED]),
            "interested": len([j for j in jobs if j.stage is Stage.SHORTLISTED]),
            "applied": len([j for j in jobs if j.stage in APPLIED_STAGES]),
            "archive": len(archived)
            + len([j for j in jobs if j.stage in ARCHIVE_STAGES]),
            "deadlines": len(
                upcoming(load_deadlines(deps.config_dir / "deadlines.yaml"), today)
            ),
        },
        "due": due_jobs(jobs, events, cfg, today),
        "deadlines": upcoming(
            load_deadlines(deps.config_dir / "deadlines.yaml"), today
        ),
    }


def search_context(store: Store, deps: Deps) -> dict:
    jobs = store.list_jobs(stage=Stage.SPOTTED)
    employers = store.list_employers()
    pollable = [e for e in employers if e.poll_enabled]
    return {
        "tabs": TABS,
        "active": "search",
        "rows": _rows(store, jobs, deps),
        "search_available": bool(pollable),
        "pollable": [e.name for e in pollable],
        "unwatched_count": len(employers) - len(pollable),
        "employers": [e.name for e in employers],
    }


def interested_context(store: Store, deps: Deps) -> dict:
    jobs = store.list_jobs(stage=Stage.SHORTLISTED)
    return {
        "tabs": TABS,
        "active": "interested",
        "rows": _rows(store, jobs, deps),
    }


def applied_context(store: Store, deps: Deps) -> dict:
    today = deps.today()
    groups: dict[str, list[JobRow]] = {s.value: [] for s in APPLIED_STAGES}
    for stage in APPLIED_STAGES:
        for row in _rows(store, store.list_jobs(stage=stage), deps):
            row.stale_days = _days_since_last_event(store, row.job, today)
            groups[stage.value].append(row)
    return {"tabs": TABS, "active": "applied", "groups": groups}


def archive_context(store: Store, deps: Deps) -> dict:
    everything = store.list_jobs(include_dismissed=True)
    archived = [
        j for j in everything if j.dismissed or j.stage in ARCHIVE_STAGES
    ]
    return {"tabs": TABS, "active": "archive", "rows": _rows(store, archived, deps)}


def deadlines_context(store: Store, deps: Deps) -> dict:
    return {
        "tabs": TABS,
        "active": "deadlines",
        "deadlines": upcoming(
            load_deadlines(deps.config_dir / "deadlines.yaml"), deps.today()
        ),
    }


WEB_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "web"


def render_tab(slug: str, context: dict,
               template_dir: Path = WEB_TEMPLATE_DIR) -> str:
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "j2"]),
    )
    return env.get_template(f"{slug}.html.j2").render(**context)
