"""SmartRecruiters — the engineering consultancies (ALTEN, Assystem, Scalian).

Consultancies advertise most heavily on LinkedIn, which cannot be polled. But
they also run a public SmartRecruiters board with the same postings on it, and
that one has an unauthenticated JSON API.
"""

from jobhunt.models import Employer
from jobhunt.sources import smartrecruiters as sr


def employer(**kw):
    base = dict(name="Scalian", ats="smartrecruiters", ats_endpoint="scalian",
                country="FR", careers_url="https://scalian.com/careers")
    base.update(kw)
    return Employer(**base)


def page(content, total=1, offset=0):
    return {"offset": offset, "limit": 100, "totalFound": total,
            "content": content}


ONE = {
    "id": "744000140008737",
    "name": "Ingénieur Navigation GNSS (H/F)",
    "refNumber": "REF6265X",
    "company": {"identifier": "Scalian", "name": "Scalian"},
    "releasedDate": "2026-07-27T13:41:41.579Z",
    "location": {"city": "Toulouse", "country": "fr", "remote": False},
    "department": {"label": "Aerospace"},
    "typeOfEmployment": {"label": "Full-time"},
    "experienceLevel": {"label": "Entry Level"},
}


def test_a_posting_becomes_an_applyable_url():
    """The API's own ref is not a page a human can apply on; the public board
    URL is."""
    postings = sr.parse_jobs(page([ONE]), employer())
    assert postings[0].url == (
        "https://jobs.smartrecruiters.com/Scalian/744000140008737"
    )


def test_the_country_comes_from_the_posting_not_the_headquarters():
    """A consultancy places people across a dozen countries. Falling back to
    the employer's country is how UK postings get stored as French ones."""
    entry = dict(ONE, location={"city": "Reading", "country": "gb"})
    postings = sr.parse_jobs(page([entry]), employer(country="FR"))
    assert postings[0].country == "GB"
    assert postings[0].city == "Reading"


def test_a_posting_with_no_location_falls_back_to_the_employer():
    entry = dict(ONE, location={})
    assert sr.parse_jobs(page([entry]), employer())[0].country == "FR"


def test_the_description_carries_department_and_seniority_for_the_matcher():
    """Only the title is indexed by these boards, so anything else that hints
    at the domain has to be handed to the matcher explicitly."""
    posting = sr.parse_jobs(page([ONE]), employer())[0]
    assert "Ingénieur Navigation GNSS" in posting.description
    assert "Aerospace" in posting.description
    assert "Entry Level" in posting.description


def test_missing_fields_never_lose_a_posting():
    postings = sr.parse_jobs(page([{"id": "1", "name": "Engineer"}]), employer())
    assert len(postings) == 1
    assert postings[0].title == "Engineer"


def test_an_empty_board_is_not_an_error():
    assert sr.parse_jobs(page([], total=0), employer()) == []


# --- pagination ---------------------------------------------------------

class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    """Serves 250 postings in pages of 100, recording the offsets asked for."""

    def __init__(self, total=250):
        self.total = total
        self.offsets = []

    def get(self, url, params=None):
        params = params or {}
        offset = int(params.get("offset", 0))
        self.offsets.append(offset)
        limit = int(params.get("limit", 100))
        remaining = max(0, self.total - offset)
        content = [dict(ONE, id=str(offset + i)) for i in range(min(limit, remaining))]
        return FakeResponse(page(content, total=self.total, offset=offset))


def test_fetch_walks_every_page():
    """A consultancy board runs to a thousand postings; one page is 8% of it."""
    client = FakeClient(total=250)
    postings = sr.fetch(employer(), client)
    assert len(postings) == 250
    assert client.offsets == [0, 100, 200]


def test_fetch_stops_at_the_page_cap():
    client = FakeClient(total=10_000)
    postings = sr.fetch(employer(), client, max_pages=2)
    assert len(postings) == 200
    assert client.offsets == [0, 100]


def test_fetch_without_an_endpoint_asks_for_nothing():
    client = FakeClient()
    assert sr.fetch(employer(ats_endpoint=""), client) == []
    assert client.offsets == []
