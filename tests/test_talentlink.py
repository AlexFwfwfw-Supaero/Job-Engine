"""TalentLink / Lumesse career portals (ONERA).

ONERA embeds a TalentLink widget, so its postings never appear in the HTML of
rejoindre.onera.fr. The widget's own REST call is reachable: the guest login
it uses is published in the page's JavaScript and is the same one every
visitor gets.

Two things make it fail silently if you get them wrong. The credentials are
literal `username`/`password` headers rather than HTTP Basic — Basic gets a
403 — and the search 500s unless sortBy and sortOrder are both sent.
"""

import pytest

from jobhunt.models import Employer
from jobhunt.sources import talentlink as tl

TID = "P6VFK026203F3VBQB68LOF6Z3"


def employer(**kw):
    base = dict(name="ONERA", ats="talentlink", ats_endpoint=TID, country="FR",
                careers_url="https://rejoindre.onera.fr/")
    base.update(kw)
    return Employer(**base)


def test_the_endpoint_may_be_just_a_site_id():
    host, tech_id = tl.parse_endpoint(TID)
    assert tech_id == TID
    assert host == tl.DEFAULT_HOST


def test_a_different_datacentre_can_be_named():
    """TalentLink is sharded by region; emea3 is not the only host."""
    host, tech_id = tl.parse_endpoint(f"emea5.recruitmentplatform.com|{TID}")
    assert (host, tech_id) == ("emea5.recruitmentplatform.com", TID)


def test_an_empty_endpoint_is_rejected():
    with pytest.raises(ValueError):
        tl.parse_endpoint("")


def test_the_guest_login_goes_in_plain_headers_not_basic_auth():
    """Sent as HTTP Basic this answers 403."""
    headers = tl.guest_headers(TID, "fr")
    assert headers["username"] == f"{TID}:guest:FO"
    assert headers["password"] == "guest"
    assert "Authorization" not in headers


ONE = {
    "id": "6625",
    "jobFields": {
        "jobTitle": "Ingénieur de recherche traitement du signal radar F/H",
        "SLOCATION": "Palaiseau",
        "REGLABEL": "Île de France",
        "CONTRACTTYPLABEL": "CDI",
        "jobNumber": "ONERA01782",
        "applicationUrl": "https://emea3.recruitmentplatform.com/apply-app/"
                          "pages/application-form?jobId=X-6625&langCode=fr_FR",
    },
    "customFields": [
        {"title": "Vos missions",
         "content": "<p>Vous développerez des algorithmes de "
                    "<b>traitement du signal</b> radar.</p>"},
        {"title": "Votre profil", "content": "<p>Jeune diplômé.</p>"},
    ],
}


def payload(jobs, total=1):
    return {"globals": {"jobsCount": total}, "jobs": jobs}


def test_a_posting_carries_its_application_url():
    posting = tl.parse_jobs(payload([ONE]), employer())[0]
    assert posting.url.endswith("jobId=X-6625&langCode=fr_FR")
    assert posting.external_id == "6625"


def test_the_title_and_city_come_through():
    posting = tl.parse_jobs(payload([ONE]), employer())[0]
    assert posting.title.startswith("Ingénieur de recherche")
    assert posting.city == "Palaiseau"


def test_the_advert_body_is_carried_as_text():
    """The custom fields hold the whole advert, so an ONERA job is readable
    by the model with no second fetch."""
    posting = tl.parse_jobs(payload([ONE]), employer())[0]
    assert "algorithmes de traitement du signal" in posting.description
    assert "<p>" not in posting.description and "<b>" not in posting.description


def test_the_contract_reaches_the_matcher():
    assert "CDI" in tl.parse_jobs(payload([ONE]), employer())[0].description


def test_the_country_is_the_employers_own():
    """The feed carries a region, not a country code, and ONERA is a French
    institution on French sites."""
    assert tl.parse_jobs(payload([ONE]), employer())[0].country == "FR"


def test_a_posting_missing_everything_optional_is_still_kept():
    postings = tl.parse_jobs(payload([{"id": "1", "jobFields": {}}]), employer())
    assert len(postings) == 1
    assert postings[0].url == "https://rejoindre.onera.fr/"


def test_an_empty_board_is_not_an_error():
    assert tl.parse_jobs(payload([], total=0), employer()) == []
    assert tl.parse_jobs({}, employer()) == []


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    PAGE = 2

    def __init__(self, total=5):
        self.total, self.calls = total, []

    def post(self, url, params=None, json=None, headers=None):
        self.calls.append({"url": url, "params": params or {},
                           "json": json, "headers": headers or {}})
        start = int((params or {}).get("firstResult", 0))
        limit = int((params or {}).get("maxResults", self.PAGE))
        jobs = [dict(ONE, id=str(start + i))
                for i in range(min(limit, max(0, self.total - start)))]
        return FakeResponse(payload(jobs, total=self.total))


def test_fetch_always_sends_the_sort_the_api_demands():
    """Without both of these the API answers 500."""
    client = FakeClient()
    tl.fetch(employer(), client, page_size=FakeClient.PAGE)
    assert client.calls[0]["params"]["sortBy"]
    assert client.calls[0]["params"]["sortOrder"]


def test_fetch_sends_the_guest_credentials():
    client = FakeClient()
    tl.fetch(employer(), client, page_size=FakeClient.PAGE)
    assert client.calls[0]["headers"]["username"].startswith(TID)


def test_fetch_pages_by_first_result():
    client = FakeClient(total=5)
    postings = tl.fetch(employer(), client, page_size=FakeClient.PAGE)
    assert len(postings) == 5
    assert [c["params"]["firstResult"] for c in client.calls] == [0, 2, 4]


def test_fetch_stops_at_the_page_cap():
    client = FakeClient(total=1000)
    assert len(tl.fetch(employer(), client, page_size=FakeClient.PAGE,
                        max_pages=2)) == 4


def test_fetch_without_an_endpoint_asks_for_nothing():
    client = FakeClient()
    assert tl.fetch(employer(ats_endpoint=""), client) == []
    assert client.calls == []
