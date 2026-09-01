from pathlib import Path

import pytest

from jobhunt.models import Employer
from jobhunt.sources import softgarden

FIXTURE = Path(__file__).parent / "fixtures" / "softgarden_jobs.html"


@pytest.fixture
def employer():
    return Employer(
        name="Fraunhofer IIS", country="DE", city="Erlangen", ats="softgarden",
        ats_endpoint="iisfraunhofer",
        careers_url="https://www.iis.fraunhofer.de/en/jobs/stellen.html",
    )


def test_the_listing_yields_postings(employer):
    postings = softgarden.parse_jobs(FIXTURE.read_text(encoding="utf-8"), employer)

    assert len(postings) == 3
    titles = [p.title for p in postings]
    assert "Embedded System Engineer – Secure GNSS Solutions (all genders)" in titles


def test_the_url_is_absolute(employer):
    """The listing links are relative to the widget, two levels up."""
    postings = softgarden.parse_jobs(FIXTURE.read_text(encoding="utf-8"), employer)
    gnss = next(p for p in postings if "GNSS" in p.title)

    assert gnss.url.startswith("https://iisfraunhofer.softgarden.io/job/64800886/")
    assert "?" not in gnss.url, "the tracking id is not part of the posting's identity"


def test_the_city_comes_off_the_row(employer):
    """IIS is spread across Erlangen, Nurnberg and Fuerth, so the employer's
    own city would be wrong for a good half of the board."""
    postings = softgarden.parse_jobs(FIXTURE.read_text(encoding="utf-8"), employer)
    gnss = next(p for p in postings if "GNSS" in p.title)
    assert gnss.city == "Nürnberg"
    assert {p.city for p in postings} == {"Erlangen", "Nürnberg"}


def test_the_requisition_id_is_kept(employer):
    postings = softgarden.parse_jobs(FIXTURE.read_text(encoding="utf-8"), employer)
    gnss = next(p for p in postings if "GNSS" in p.title)
    assert gnss.external_id == "64800886"


def test_a_student_row_is_marked_as_such(employer):
    """softgarden states the audience, which is better evidence than guessing
    from the title: "Werkstudent*in" is a working student, not a graduate
    engineer, and it should not be priced as one."""
    postings = softgarden.parse_jobs(FIXTURE.read_text(encoding="utf-8"), employer)
    student = next(p for p in postings if "Werkstudent" in p.title)
    assert student.level == "student"
    apprentice = next(p for p in postings if "Auszubildende" in p.title)
    assert apprentice.level == "student"


def test_an_experienced_row_stays_junior(employer):
    postings = softgarden.parse_jobs(FIXTURE.read_text(encoding="utf-8"), employer)
    gnss = next(p for p in postings if "GNSS" in p.title)
    assert gnss.level == "junior"


def test_rows_carry_no_advert_so_enrichment_fetches_one(employer):
    """The listing gives a title, a city and an audience. The advert is on the
    detail page, which is server-rendered and readable."""
    postings = softgarden.parse_jobs(FIXTURE.read_text(encoding="utf-8"), employer)
    assert all(p.description == "" for p in postings)


class FakePage:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return FakePage(self.text)


def test_fetch_asks_the_widget_for_the_whole_board(employer):
    client = FakeClient(FIXTURE.read_text(encoding="utf-8"))
    postings = softgarden.fetch(employer, client)

    assert len(postings) == 3
    assert client.urls[0].startswith(
        "https://iisfraunhofer.softgarden.io/en/widgets/jobs")


def test_an_endpoint_that_is_already_a_url_is_used_as_given(employer):
    """Tenants that publish under their own host rather than softgarden.io."""
    employer.ats_endpoint = "https://careers.example.com/en/widgets/jobs"
    client = FakeClient(FIXTURE.read_text(encoding="utf-8"))
    softgarden.fetch(employer, client)

    assert client.urls[0] == "https://careers.example.com/en/widgets/jobs"


def test_no_endpoint_means_no_postings(employer):
    employer.ats_endpoint = ""
    assert softgarden.fetch(employer, FakeClient("")) == []


def test_the_source_is_registered():
    from jobhunt.poll import SOURCE_REGISTRY, UNPAGINATED

    assert SOURCE_REGISTRY["softgarden"] is softgarden.fetch
    assert "softgarden" in UNPAGINATED, "the widget returns the whole board at once"
