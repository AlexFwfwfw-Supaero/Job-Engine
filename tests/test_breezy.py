import json
from pathlib import Path

from jobhunt.models import Employer
from jobhunt.sources import breezy

FIXTURE = Path(__file__).parent / "fixtures" / "breezy_telespazio.json"


def telespazio():
    return Employer(
        id=1, name="Telespazio Belgium", country="BE", city="Brussels",
        ats="breezy", ats_endpoint="https://telespazio-be.breezy.hr/json",
        careers_url="https://telespazio-be.breezy.hr/",
    )


def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_jobs_reads_title_location_and_url():
    postings = breezy.parse_jobs(payload(), telespazio())
    by_title = {p.title: p for p in postings}
    egnos = by_title["EGNOS Service Provision security support Engineer"]
    assert egnos.city == "Toulouse"
    assert egnos.country == "FR"
    assert egnos.url.startswith("https://telespazio-be.breezy.hr/p/")
    assert egnos.source == breezy.NAME


def test_parse_jobs_reads_every_position():
    assert len(breezy.parse_jobs(payload(), telespazio())) == len(payload())


def test_parse_jobs_uses_the_department_as_description():
    """Breezy's listing feed carries no body text; the department is the only
    extra signal, and for Telespazio it names the ESA site and contract."""
    postings = breezy.parse_jobs(payload(), telespazio())
    assert any("ESTEC" in p.description for p in postings)


def test_parse_jobs_tolerates_missing_fields():
    (posting,) = breezy.parse_jobs([{"name": "Engineer"}], telespazio())
    assert posting.title == "Engineer"
    assert posting.city == ""
    assert posting.country == "BE"
    assert posting.url == "https://telespazio-be.breezy.hr/"


def test_parse_jobs_ignores_a_non_list_payload():
    assert breezy.parse_jobs({"error": "nope"}, telespazio()) == []


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


def test_fetch_requests_the_configured_endpoint_once():
    client = FakeClient(payload())
    postings = breezy.fetch(telespazio(), client)
    assert client.urls == ["https://telespazio-be.breezy.hr/json"]
    assert len(postings) == len(payload())


def test_fetch_without_an_endpoint_returns_nothing():
    employer = telespazio()
    employer.ats_endpoint = ""
    assert breezy.fetch(employer, FakeClient([])) == []
