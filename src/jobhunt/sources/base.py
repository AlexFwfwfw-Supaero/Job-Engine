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
