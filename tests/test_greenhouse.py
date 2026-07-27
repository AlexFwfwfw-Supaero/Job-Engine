import json
from pathlib import Path

from jobhunt.models import Employer
from jobhunt.sources import greenhouse

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_isar.json"


def isar():
    return Employer(
        id=1, name="Isar Aerospace", country="DE", city="Ottobrunn",
        ats="greenhouse", ats_endpoint="isaraerospace",
        careers_url="https://isaraerospace.com/career",
    )


def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_board_url_is_built_from_the_board_token():
    assert greenhouse.board_url("isaraerospace") == (
        "https://boards-api.greenhouse.io/v1/boards/isaraerospace/jobs"
    )


def test_board_url_accepts_a_full_url_unchanged():
    """Config may carry either a token or the endpoint someone pasted in."""
    url = "https://boards-api.greenhouse.io/v1/boards/x/jobs"
    assert greenhouse.board_url(url) == url


def test_parse_jobs_reads_title_url_and_city():
    postings = greenhouse.parse_jobs(payload(), isar())
    ait = next(p for p in postings if p.title.startswith("AIT Operations Engineer"))
    assert ait.url == "https://job-boards.eu.greenhouse.io/isaraerospace/jobs/4843468101"
    assert ait.city == "Ottobrunn"
    assert ait.country == "DE"
    assert ait.source == greenhouse.NAME


def test_parse_jobs_maps_the_country_name_to_an_iso_code():
    """Location is one free-text string; the matcher filters on ISO codes,
    so 'Kiruna, Norrbotten, Sweden' has to become SE or UK exclusion breaks."""
    postings = greenhouse.parse_jobs(payload(), isar())
    kiruna = next(p for p in postings if p.city == "Kiruna")
    assert kiruna.country == "SE"


def test_parse_jobs_maps_the_united_kingdom_so_exclusion_can_fire():
    jobs = {"jobs": [{"id": 1, "title": "Tech", "absolute_url": "u",
                      "location": {"name": "Shetland Islands, Scotland, "
                                           "United Kingdom"}}]}
    (posting,) = greenhouse.parse_jobs(jobs, isar())
    assert posting.country == "GB"


def test_parse_jobs_falls_back_to_the_employer_country_when_unmapped():
    jobs = {"jobs": [{"id": 1, "title": "Tech", "absolute_url": "u",
                      "location": {"name": "Somewhere Odd"}}]}
    (posting,) = greenhouse.parse_jobs(jobs, isar())
    assert posting.country == "DE"


def test_parse_jobs_takes_the_first_of_several_listed_locations():
    jobs = {"jobs": [{"id": 1, "title": "Tech", "absolute_url": "u",
                      "location": {"name": "Ottobrunn, Bavaria, Germany; "
                                           "Parsdorf, Bavaria, Germany"}}]}
    (posting,) = greenhouse.parse_jobs(jobs, isar())
    assert posting.city == "Ottobrunn"


def test_parse_jobs_handles_a_missing_location():
    jobs = {"jobs": [{"id": 1, "title": "Tech", "absolute_url": "u"}]}
    (posting,) = greenhouse.parse_jobs(jobs, isar())
    assert posting.city == ""
    assert posting.country == "DE"


def test_parse_jobs_returns_nothing_for_an_empty_board():
    assert greenhouse.parse_jobs({"jobs": []}, isar()) == []


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, data):
        self.data = data
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return FakeResponse(self.data)


def test_fetch_requests_the_board_once():
    client = FakeClient(payload())
    postings = greenhouse.fetch(isar(), client)
    assert client.urls == [
        "https://boards-api.greenhouse.io/v1/boards/isaraerospace/jobs"
    ]
    assert len(postings) == 94


def test_fetch_without_an_endpoint_returns_nothing():
    employer = isar()
    employer.ats_endpoint = ""
    assert greenhouse.fetch(employer, FakeClient({})) == []
