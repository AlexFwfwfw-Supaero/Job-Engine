from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobhunt.config import load_cities, load_comp, load_deadlines, load_scoring
from jobhunt.deadlines import upcoming
from jobhunt.linkedin import build_links, company_links, locations_from_cities
from jobhunt.models import Job, Stage
from jobhunt.score import compensation
from jobhunt.staleness import due_jobs
from jobhunt.store import Store
from jobhunt.web.deps import Deps
from jobhunt.web.progress import ADVICE, POLL, PROGRESS

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
    ("advice", "Advice",
     "One read across every job: where to apply, and what the employers are like."),
]

APPLIED_STAGES = [Stage.APPLIED, Stage.SCREENING, Stage.INTERVIEW, Stage.OFFER]
ARCHIVE_STAGES = [Stage.REJECTED, Stage.WITHDRAWN, Stage.EXPIRED]


@dataclass
class JobRow:
    job: Job
    employer_name: str
    comp_label: str
    stale_days: int | None = None
    # The model's reading, decoded for the template. None when never analysed.
    ai: dict | None = None


def _ai(job: Job) -> dict | None:
    if not job.llm_json:
        return None
    try:
        return json.loads(job.llm_json)
    except json.JSONDecodeError:
        return None


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
            ai=_ai(job),
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
            # How many jobs the standing briefing covers — 0 until one is run.
            "advice": getattr(store.latest_briefing(), "job_count", 0),
        },
        "due": due_jobs(jobs, events, cfg, today),
        "deadlines": upcoming(
            load_deadlines(deps.config_dir / "deadlines.yaml"), today
        ),
    }


def _group_links(links) -> list[tuple[str, list]]:
    groups: dict[str, list] = {}
    for link in links:
        groups.setdefault(link.location, []).append(link)
    return sorted(groups.items())


def _linkedin_groups(cfg, cities) -> list[tuple[str, list]]:
    """LinkedIn search links grouped by location, one group per city."""
    return _group_links(build_links(cfg, locations_from_cities(cities)))


# How the Spotted table can be ordered, highest first. The default blends the
# model's fit with compensation and quality of life, which is right for
# deciding where to apply but buries a strong read under a weak salary band;
# "ai" is that read on its own. Each key returns a sort key, not a comparison.
SPOTTED_SORTS: dict[str, tuple[str, Callable[[JobRow], float]]] = {
    "rank": ("Rank", lambda row: row.job.rank_score or 0.0),
    # Unread jobs have no reading to sort on, so they go last rather than
    # landing among the zeros as if the model had rejected them.
    "ai": ("AI fit", lambda row: -1.0 if row.job.llm_fit is None
           else row.job.llm_fit),
    "score": ("Score", lambda row: row.job.total_score or 0.0),
}
DEFAULT_SPOTTED_SORT = "rank"


def sort_rows(rows: list[JobRow], sort: str) -> list[JobRow]:
    """Order the Spotted table. An unknown sort falls back to the default
    rather than erroring: the value arrives from a query string."""
    _, key = SPOTTED_SORTS.get(sort, SPOTTED_SORTS[DEFAULT_SPOTTED_SORT])
    return sorted(rows, key=key, reverse=True)


def search_context(store: Store, deps: Deps,
                   sort: str = DEFAULT_SPOTTED_SORT) -> dict:
    jobs = store.list_jobs(stage=Stage.SPOTTED)
    employers = store.list_employers()
    pollable = [e for e in employers if e.poll_enabled]
    cfg, _comp_cfg, cities = _load_config(deps)
    return {
        "linkedin_groups": _linkedin_groups(cfg, cities),
        "consultancy_groups": _group_links(company_links(cfg)),
        "ai_available": deps.llm() is not None,
        # Rendered on first paint so the status is right without JavaScript;
        # the page's poller rewrites the same two lines from /api/progress.
        "ai_progress": PROGRESS,
        "poll_progress": POLL,
        "tabs": TABS,
        "active": "search",
        "rows": sort_rows(_rows(store, jobs, deps), sort),
        "sort": sort if sort in SPOTTED_SORTS else DEFAULT_SPOTTED_SORT,
        "sorts": [(key, label) for key, (label, _) in SPOTTED_SORTS.items()],
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
        "ai_available": deps.llm() is not None,
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


def advice_context(store: Store, deps: Deps) -> dict:
    briefing = store.latest_briefing()
    jobs = store.list_jobs()
    return {
        "tabs": TABS,
        "active": "advice",
        "briefing": briefing,
        "ai_available": deps.llm() is not None,
        "ai_progress": ADVICE,
        "job_count": len(jobs),
        "shortlist_count": len([j for j in jobs if j.stage is Stage.SHORTLISTED]),
        "unread_count": len([j for j in jobs if j.llm_fit is None]),
    }


WEB_TEMPLATE_DIR =Path(__file__).resolve().parent.parent / "templates" / "web"


def render_tab(slug: str, context: dict,
               template_dir: Path = WEB_TEMPLATE_DIR) -> str:
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "j2"]),
    )
    return env.get_template(f"{slug}.html.j2").render(**context)
