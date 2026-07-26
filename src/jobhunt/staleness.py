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
