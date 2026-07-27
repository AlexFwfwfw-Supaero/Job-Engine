from pathlib import Path

import pytest

from jobhunt.models import Employer
from jobhunt.sources.euraxess import (
    ENGINEERING_FIELD, FIRST_STAGE_RESEARCHER, build_search_url, fetch,
    parse_results,
)

FIXTURE = Path(__file__).parent / "fixtures" / "euraxess_engineering.html"


@pytest.fixture
def page():
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def employer():
    return Employer(name="EURAXESS", ats="euraxess", tags=["phd", "research"])


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return FakeResponse(self.pages.pop(0) if self.pages else "")


def test_parses_every_result_block(page, employer):
    postings = parse_results(page, employer)
    assert len(postings) == 3
    assert all(p.source == "euraxess" for p in postings)


def test_extracts_titles(page, employer):
    titles = [p.title for p in parse_results(page, employer)]
    assert titles[0].startswith("PhD Stipend in Dynamic Modelling")


def test_builds_absolute_urls(page, employer):
    urls = [p.url for p in parse_results(page, employer)]
    assert urls[0] == "https://euraxess.ec.europa.eu/jobs/455925"


def test_extracts_the_hosting_organisation_as_employer(page, employer):
    names = [p.employer_name for p in parse_results(page, employer)]
    assert "Aalborg Universitet" in names[0]


def test_description_is_captured_for_matching(page, employer):
    first = parse_results(page, employer)[0]
    assert "Power-to-X" in first.description


def test_external_id_is_the_numeric_job_id(page, employer):
    assert parse_results(page, employer)[0].external_id == "455925"


def test_empty_html_yields_nothing(employer):
    assert parse_results("", employer) == []
    assert parse_results("<html><body>no results</body></html>", employer) == []


def test_search_url_applies_the_engineering_facet():
    url = build_search_url(page=0)
    assert "job_research_field%3A164" in url
    assert ENGINEERING_FIELD == 164


def test_search_url_can_add_the_first_stage_researcher_facet():
    url = build_search_url(page=0, first_stage_only=True)
    assert "job_research_profile%3A447" in url
    assert FIRST_STAGE_RESEARCHER == 447


def test_search_url_paginates():
    assert "page=2" in build_search_url(page=2)
    assert "page=" not in build_search_url(page=0)


def test_fetch_walks_pages_and_stops_when_empty(page, employer):
    client = FakeClient([page, page, ""])
    postings = fetch(employer, client, max_pages=5)
    assert len(postings) == 6
    assert len(client.calls) == 3


def test_fetch_respects_max_pages(page, employer):
    client = FakeClient([page, page, page, page])
    fetch(employer, client, max_pages=2)
    assert len(client.calls) == 2
