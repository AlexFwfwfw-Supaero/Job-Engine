"""Pre-built LinkedIn job-search links.

LinkedIn has no public job API, and its User Agreement forbids scraping the
site — anything that fetched results automatically would be both fragile and
against the terms. Since a large share of the interesting GNSS and PNT roles
are advertised there, the useful thing this module can do is remove the
tedious part: it builds the exact search URLs worth opening, so the searches
stay consistent between sessions and nothing is forgotten. Anything found
goes back into the tracker through manual entry, which is already first-class.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from jobhunt.config import City, ScoringConfig

# LinkedIn matches locations by display name, not ISO code: "Toulouse, FR"
# resolves to nothing while "Toulouse, France" resolves correctly.
COUNTRY_NAMES = {
    "AT": "Austria", "BE": "Belgium", "CH": "Switzerland", "DE": "Germany",
    "DK": "Denmark", "ES": "Spain", "FI": "Finland", "FR": "France",
    "IE": "Ireland", "IT": "Italy", "LU": "Luxembourg", "NL": "Netherlands",
    "NO": "Norway", "PL": "Poland", "PT": "Portugal", "SE": "Sweden",
}

BASE_URL = "https://www.linkedin.com/jobs/search/"
# Postings from the last week, newest first. An unfiltered LinkedIn search
# surfaces long-dead listings, which makes the whole exercise feel useless.
RECENT_WINDOW = "r604800"
SORT_BY_DATE = "DD"


@dataclass(frozen=True)
class SearchLink:
    term: str
    location: str
    url: str

    @property
    def label(self) -> str:
        return f"{self.term} — {self.location}" if self.location else self.term


def search_url(term: str, location: str) -> str:
    params = {"keywords": term, "f_TPR": RECENT_WINDOW, "sortBy": SORT_BY_DATE}
    if location:
        params["location"] = location
    return f"{BASE_URL}?{urlencode(params)}"


def locations_from_cities(cities: dict[str, City]) -> list[str]:
    """LinkedIn-resolvable location strings for the cities you already score.

    Cities whose country code is not in COUNTRY_NAMES are skipped rather than
    guessed: a location LinkedIn cannot resolve silently returns every job in
    the world, which is worse than leaving the city out.
    """
    seen: list[str] = []
    for city in cities.values():
        country = COUNTRY_NAMES.get(city.country.upper())
        if not country:
            continue
        label = f"{city.name}, {country}"
        if label not in seen:
            seen.append(label)
    return sorted(seen)


def build_links(cfg: ScoringConfig, locations: list[str]) -> list[SearchLink]:
    """Every configured search term crossed with every target location."""
    places = list(locations) or [""]
    return [
        SearchLink(term=term, location=place, url=search_url(term, place))
        for term in cfg.poll_search_terms
        for place in places
    ]
