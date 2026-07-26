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
