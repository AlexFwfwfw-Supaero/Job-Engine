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
