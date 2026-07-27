from pathlib import Path

from jobhunt.models import Employer
from jobhunt.sources import successfactors

FIXTURE = Path(__file__).parent / "fixtures" / "successfactors_esa.html"


def esa():
    return Employer(
        id=1, name="ESA", country="NL", city="Noordwijk",
        ats="successfactors", ats_endpoint="https://jobs.esa.int",
        careers_url="https://jobs.esa.int/",
    )


def test_build_search_url_paginates_with_startrow():
    url = successfactors.build_search_url("https://jobs.esa.int", startrow=25)
    assert url.startswith("https://jobs.esa.int/search/?")
    assert "startrow=25" in url


def test_build_search_url_tolerates_a_trailing_slash():
    url = successfactors.build_search_url("https://jobs.esa.int/", startrow=0)
    assert "//search" not in url.replace("https://", "")


def test_parse_jobs_reads_title_location_and_url():
    postings = successfactors.parse_jobs(FIXTURE.read_text(encoding="utf-8"), esa())
    by_title = {p.title: p for p in postings}
    assert "Lead Payload Engineer" in by_title
    lead = by_title["Lead Payload Engineer"]
    assert lead.city == "Noordwijk"
    assert lead.country == "NL"
    assert lead.url == "https://jobs.esa.int/job/Noordwijk-Lead-Payload-Engineer/1418258133/"
    assert lead.external_id == "1418258133"
    assert lead.source == successfactors.NAME


def test_parse_jobs_deduplicates_the_repeated_desktop_and_mobile_tiles():
    """Each job appears three times in the markup, once per responsive layout."""
    postings = successfactors.parse_jobs(FIXTURE.read_text(encoding="utf-8"), esa())
    assert len(postings) == 3
    assert len({p.url for p in postings}) == 3


def test_parse_jobs_unescapes_entities_in_titles():
    postings = successfactors.parse_jobs(FIXTURE.read_text(encoding="utf-8"), esa())
    titles = {p.title for p in postings}
    assert "RAMS (Reliability, Availability, Maintainability and Safety) Engineer" in titles


def test_parse_jobs_falls_back_to_employer_country_when_workplace_is_missing():
    html = '<a class="jobTitle-link" href="/job/Somewhere-Thing/999/">Thing</a>'
    (posting,) = successfactors.parse_jobs(html, esa())
    assert posting.country == "NL"
    assert posting.city == ""


def test_parse_jobs_returns_nothing_for_an_empty_page():
    assert successfactors.parse_jobs("<html><body>no jobs</body></html>", esa()) == []


def test_total_jobs_reads_the_result_count():
    assert successfactors.total_jobs(FIXTURE.read_text(encoding="utf-8")) == 51


def test_total_jobs_is_none_when_the_count_is_absent():
    assert successfactors.total_jobs("<html></html>") is None


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return FakeResponse(self.pages.pop(0) if self.pages else "")


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def test_fetch_stops_when_a_page_returns_no_jobs():
    client = FakeClient([FIXTURE.read_text(encoding="utf-8"), "<html></html>"])
    postings = successfactors.fetch(esa(), client, max_pages=5)
    assert len(postings) == 3
    assert len(client.urls) == 2


def test_fetch_without_an_endpoint_returns_nothing():
    employer = esa()
    employer.ats_endpoint = ""
    assert successfactors.fetch(employer, FakeClient([])) == []


def beyond_gravity():
    return Employer(
        id=2, name="Beyond Gravity", country="CH", city="Zurich",
        ats="successfactors",
        ats_endpoint="https://careers.beyondgravity.com/BeyondGravity",
        careers_url="https://careers.beyondgravity.com/BeyondGravity/search/",
    )


def test_parse_jobs_handles_a_site_segment_before_the_job_path():
    """Beyond Gravity serves /BeyondGravity/job/...; ESA serves /job/...

    A regex anchored on '/job/' silently returned zero postings for the whole
    tenant, which reads as an empty job market rather than a parser bug.
    """
    html = (
        '<a class="jobTitle-link fontcolor95" data-focus-tile=".job-id-1397475133"'
        ' href="/BeyondGravity/job/Trinity-Analysis-Engineer-AL-35673/1397475133/">'
        "\n   Analysis Engineer\n</a>"
    )
    (posting,) = successfactors.parse_jobs(html, beyond_gravity())
    assert posting.title == "Analysis Engineer"
    assert posting.external_id == "1397475133"
    assert posting.url == (
        "https://careers.beyondgravity.com/BeyondGravity/job/"
        "Trinity-Analysis-Engineer-AL-35673/1397475133/"
    )


def test_parse_jobs_does_not_duplicate_the_site_segment_in_the_url():
    html = '<a class="jobTitle-link" href="/BeyondGravity/job/X/1/">X</a>'
    (posting,) = successfactors.parse_jobs(html, beyond_gravity())
    assert posting.url.count("/BeyondGravity/") == 1
