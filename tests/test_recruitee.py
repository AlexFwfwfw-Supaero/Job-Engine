"""Recruitee — the ESA support contractors (ATG Europe).

Much of the work at ESTEC, ESOC and ESRIN is advertised by contractors rather
than by ESA itself, so a watchlist that stops at jobs.esa.int misses most of
the roles physically located at ESA sites.
"""

from jobhunt.models import Employer
from jobhunt.sources import recruitee


def employer(**kw):
    base = dict(name="ATG Europe", ats="recruitee", ats_endpoint="atgeurope",
                country="NL", careers_url="https://jobs.atg-europe.com")
    base.update(kw)
    return Employer(**base)


ONE = {
    "id": 2684601,
    "title": "GNSS Navigation Engineer",
    "city": "Noordwijk",
    "country_code": "NL",
    "location": "Noordwijk, Zuid-Holland, Netherlands",
    "careers_url": "https://jobs.atg-europe.com/o/gnss-navigation-engineer",
    "department": "ESA/ESTEC",
    "employment_type_code": "fulltime_fixed_term",
}


def test_a_posting_keeps_its_own_apply_url():
    posting = recruitee.parse_jobs({"offers": [ONE]}, employer())[0]
    assert posting.url == "https://jobs.atg-europe.com/o/gnss-navigation-engineer"
    assert posting.external_id == "2684601"


def test_the_country_comes_from_the_posting():
    entry = dict(ONE, city="Darmstadt", country_code="DE")
    posting = recruitee.parse_jobs({"offers": [entry]}, employer())[0]
    assert (posting.city, posting.country) == ("Darmstadt", "DE")


def test_the_department_reaches_the_matcher():
    """'ESA/ESTEC' in the department is the strongest signal these postings
    carry about where the work actually happens."""
    posting = recruitee.parse_jobs({"offers": [ONE]}, employer())[0]
    assert "ESA/ESTEC" in posting.description


def test_a_missing_country_falls_back_to_the_employer():
    entry = dict(ONE)
    del entry["country_code"]
    assert recruitee.parse_jobs({"offers": [entry]}, employer())[0].country == "NL"


def test_an_empty_board_is_not_an_error():
    assert recruitee.parse_jobs({"offers": []}, employer()) == []
    assert recruitee.parse_jobs({}, employer()) == []


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_fetch_builds_the_board_url_from_the_slug():
    seen = {}

    class Client:
        def get(self, url):
            seen["url"] = url
            return FakeResponse({"offers": [ONE]})

    assert len(recruitee.fetch(employer(), Client())) == 1
    assert seen["url"] == "https://atgeurope.recruitee.com/api/offers/"


def test_fetch_without_an_endpoint_asks_for_nothing():
    class Client:
        def get(self, url):
            raise AssertionError("should not be called")

    assert recruitee.fetch(employer(ats_endpoint=""), Client()) == []
