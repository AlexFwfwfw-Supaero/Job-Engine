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
    notes: str = ""
    priority: int = 0
    link_status: str = "unknown"
    last_checked: str | None = None
    # Full posting text, fetched on demand. Empty for jobs never enriched.
    description: str = ""
    # The language model's own reading, kept beside the rule-based scores
    # rather than folded into them: a model's confident wrong answer must
    # never silently outrank a deterministic match.
    llm_fit: float | None = None
    llm_json: str = ""
    llm_checked: str | None = None
    # What the lists sort by: the weighted score with the model's fit
    # substituted in once it has read the posting. See score.ranking_score.
    rank_score: float = 0.0
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
