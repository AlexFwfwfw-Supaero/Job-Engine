"""One model pass across the whole set: what to apply to, and what it is like.

`enrich` answers "does this posting match" one posting at a time. This answers
the question that decides an application — career prospects, pay, what the
employer is like inside, which doors it opens — across every tracked job at
once. That is one call, not one per job, so it is cheap enough to re-run
whenever the list changes.

The result is stored. A briefing costs a call, the browser would otherwise
regenerate it on every page load, and the CLI and the UI must show the same
advice rather than two independently-worded readings.
"""

from __future__ import annotations

from jobhunt.llm import advise_prompt
from jobhunt.models import Briefing, Job
from jobhunt.store import Store

# Four sections over dozens of jobs does not fit the per-verdict budget, and a
# truncated briefing silently loses its last section.
BRIEFING_MAX_TOKENS = 8000


def build_briefing(
    store: Store,
    jobs: list[Job],
    profile: str,
    places: str,
    llm,
    now: str,
    scope: str = "all",
) -> Briefing | None:
    """Ask for the briefing, store it, return it. None when there is nothing
    to advise on — never spend a call to be told the list is empty."""
    if not jobs:
        return None

    employers = {e.id: e.name for e in store.list_employers()}
    prompt = advise_prompt(jobs, profile, places, employers=employers)
    text = llm.complete(prompt, max_tokens=BRIEFING_MAX_TOKENS)

    briefing = Briefing(ts=now, text=text, scope=scope, job_count=len(jobs))
    briefing.id = store.save_briefing(now, text, scope=scope,
                                      job_count=len(jobs))
    return briefing
