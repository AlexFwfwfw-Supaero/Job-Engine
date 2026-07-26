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
