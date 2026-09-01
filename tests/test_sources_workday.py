import json
from pathlib import Path

import pytest

from jobhunt.models import Employer
from jobhunt.sources.workday import fetch, parse_jobs

FIXTURE = Path(__file__).parent / "fixtures" / "workday_airbus.json"


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def employer():
    return Employer(
        name="Airbus Defence and Space",
        ats="workday",
        ats_endpoint="https://ag.wd3.myworkdayjobs.com/wday/cxs/ag/Airbus/jobs",
        careers_url="https://ag.wd3.myworkdayjobs.com/en-US/Airbus",
        country="DE",
    )


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def post(self, url, json=None, **kwargs):
        self.calls.append((url, json))
        return FakeResponse(self.pages.pop(0) if self.pages else {"jobPostings": []})


def test_parse_returns_a_posting_per_job(payload, employer):
    postings = parse_jobs(payload, employer)
    assert len(postings) == len(payload["jobPostings"])
    assert postings[0].source == "workday"
    assert postings[0].employer_name == "Airbus Defence and Space"


def test_parse_builds_an_absolute_url(payload, employer):
    url = parse_jobs(payload, employer)[0].url
    assert url.startswith("https://ag.wd3.myworkdayjobs.com/en-US/Airbus/job/")


def test_parse_keeps_the_title_and_location(payload, employer):
    titles = [p.title for p in parse_jobs(payload, employer)]
    assert any("Navigation" in t for t in titles)
    located = [p for p in parse_jobs(payload, employer) if p.city]
    assert located


def test_parse_survives_missing_optional_fields(employer):
    """Live Workday responses omit locationsText on some postings."""
    payload = {"jobPostings": [
        {"title": "Bare Minimum Engineer", "externalPath": "/job/X/Bare_JR1"},
        {"externalPath": "/job/Y/NoTitle_JR2"},
        {"title": "No Path Engineer"},
    ]}
    postings = parse_jobs(payload, employer)
    assert [p.title for p in postings] == ["Bare Minimum Engineer", "", "No Path Engineer"]
    assert postings[0].city == ""
    assert postings[2].url == employer.careers_url


def test_parse_records_the_external_id(payload, employer):
    ids = [p.external_id for p in parse_jobs(payload, employer)]
    assert any(i.startswith("JR") for i in ids)


def test_parse_empty_payload_is_empty(employer):
    assert parse_jobs({}, employer) == []
    assert parse_jobs({"jobPostings": []}, employer) == []


def test_fetch_posts_to_the_configured_endpoint(payload, employer):
    client = FakeClient([payload])
    fetch(employer, client, page_size=5, max_pages=1)
    url, body = client.calls[0]
    assert url == employer.ats_endpoint
    assert body["limit"] == 5
    assert body["offset"] == 0


def test_fetch_paginates_until_a_short_page(employer):
    full = {"jobPostings": [
        {"title": f"Job {i}", "externalPath": f"/job/X/J{i}"} for i in range(3)
    ]}
    short = {"jobPostings": [{"title": "Last", "externalPath": "/job/X/last"}]}
    client = FakeClient([full, short])
    postings = fetch(employer, client, page_size=3, max_pages=5)
    assert len(postings) == 4
    assert [body["offset"] for _, body in client.calls] == [0, 3]


def test_fetch_respects_max_pages(employer):
    full = {"jobPostings": [
        {"title": f"Job {i}", "externalPath": f"/job/X/J{i}"} for i in range(2)
    ]}
    client = FakeClient([full, full, full, full])
    fetch(employer, client, page_size=2, max_pages=2)
    assert len(client.calls) == 2


def test_fetch_without_an_endpoint_returns_nothing(employer):
    employer.ats_endpoint = ""
    assert fetch(employer, FakeClient([]), page_size=5, max_pages=1) == []


def test_fetch_queries_once_per_search_term(employer):
    one = {"jobPostings": [{"title": "GNSS Engineer", "externalPath": "/job/X/a"}]}
    client = FakeClient([one, one, one])
    fetch(employer, client, page_size=20, max_pages=1,
          search_terms=["gnss", "radar", "kalman"])
    assert [body["searchText"] for _, body in client.calls] == [
        "gnss", "radar", "kalman"]


def test_fetch_unions_search_terms_without_duplicates(employer):
    same = {"jobPostings": [{"title": "GNSS Engineer", "externalPath": "/job/X/a"}]}
    other = {"jobPostings": [{"title": "Radar Engineer", "externalPath": "/job/X/b"}]}
    client = FakeClient([same, other, same])
    postings = fetch(employer, client, page_size=20, max_pages=1,
                     search_terms=["gnss", "radar", "galileo"])
    assert sorted(p.title for p in postings) == ["GNSS Engineer", "Radar Engineer"]


def test_fetch_with_no_terms_falls_back_to_one_broad_query(employer, payload):
    client = FakeClient([payload])
    fetch(employer, client, page_size=5, max_pages=1, search_terms=[])
    assert [body["searchText"] for _, body in client.calls] == [""]


def test_a_failing_term_does_not_lose_the_others(employer):
    class FlakyClient(FakeClient):
        def post(self, url, json=None, **kwargs):
            self.calls.append((url, json))
            if json["searchText"] == "radar":
                raise RuntimeError("upstream hiccup")
            return FakeResponse({"jobPostings": [
                {"title": "GNSS Engineer", "externalPath": "/job/X/a"}]})

    postings = fetch(employer, FlakyClient([]), page_size=20, max_pages=1,
                     search_terms=["gnss", "radar", "kalman"])
    assert [p.title for p in postings] == ["GNSS Engineer"]


# --- locations that state their own country -----------------------------

def test_a_country_prefixed_location_is_believed_over_the_employers():
    """Leonardo writes "IT - Torino - C.so Francia" and its tenant carries
    Italian, British, German and Polish sites on one board. Stamping them all
    with the employer's country puts Yeovil and Basildon — both excluded — in
    the results as Italian jobs."""
    from jobhunt.sources.workday import split_location

    assert split_location("IT - Torino - C.so Francia") == ("Torino", "IT")
    assert split_location("GB - Yeovil - Lysander Rd") == ("Yeovil", "GB")
    assert split_location("DE - Darmstadt - ESOC") == ("Darmstadt", "DE")


def test_a_plain_location_keeps_the_employers_country():
    """Airbus and Thales write "Toulouse Area" with no country prefix, so
    there is nothing to read and the employer's own stays."""
    from jobhunt.sources.workday import split_location

    assert split_location("Toulouse Area") == ("Toulouse Area", "")
    assert split_location("2 Locations") == ("2 Locations", "")
    assert split_location("") == ("", "")


def test_a_lookalike_prefix_is_not_mistaken_for_a_country():
    """Only a two-letter uppercase code counts. Without one, the string is
    left exactly as the tenant wrote it rather than being carved up on a
    separator that means nothing here — "Rome - Via Tiburtina" is a street
    address, and guessing which half is the town is how "Toulouse Area"
    became a city nobody could match."""
    from jobhunt.sources.workday import split_location

    assert split_location("Rome - Via Tiburtina") == ("Rome - Via Tiburtina", "")
    assert split_location("us - Melbourne") == ("us - Melbourne", "")
    assert split_location("US - Melbourne - FL") == ("Melbourne", "US")


def test_parse_jobs_uses_the_stated_country(employer):
    from jobhunt.sources.workday import parse_jobs

    payload = {"jobPostings": [
        {"title": "Radar Systems Engineer", "externalPath": "/job/x_R1",
         "locationsText": "GB - Edinburgh"},
        {"title": "Astro Dynamics Engineer", "externalPath": "/job/y_R2",
         "locationsText": "DE - Darmstadt - ESOC"},
    ]}
    postings = parse_jobs(payload, employer)

    assert [(p.city, p.country) for p in postings] == [
        ("Edinburgh", "GB"), ("Darmstadt", "DE")]


def test_a_multi_location_posting_falls_back_to_its_path(employer):
    """Workday collapses a posting advertised at several sites into
    "2 Locations", which states no country at all — and five of Leonardo's ten
    stored jobs came back that way, filed as Italian while their paths said
    GB - Edinburgh. The externalPath keeps the primary site."""
    from jobhunt.sources.workday import parse_jobs

    payload = {"jobPostings": [{
        "title": "Systems Engineer - Antenna/Electromagnetic",
        "externalPath": "/job/GB---Edinburgh/Systems-Engineer---Antenna_R1",
        "locationsText": "2 Locations",
    }]}
    posting = parse_jobs(payload, employer)[0]

    assert posting.country == "GB"
    assert posting.city == "Edinburgh"


def test_a_path_without_a_country_leaves_the_employers_own(employer):
    from jobhunt.sources.workday import parse_jobs

    payload = {"jobPostings": [{
        "title": "GNSS Engineer", "externalPath": "/job/Toulouse-Area/GNSS_R2",
        "locationsText": "2 Locations",
    }]}
    posting = parse_jobs(payload, employer)[0]

    assert posting.country == employer.country
