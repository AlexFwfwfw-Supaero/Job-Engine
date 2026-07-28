"""Capgemini's own job API ("jobstream").

One company, one bespoke API, so this module is named for the company rather
than a platform. It earns its place: the board carries the full posting text,
so a Capgemini job arrives already readable by the model with no second fetch.
"""

from jobhunt.models import Employer
from jobhunt.sources import capgemini


def employer(**kw):
    base = dict(name="Capgemini Engineering", ats="capgemini",
                ats_endpoint="fr-fr|Capgemini Engineering", country="FR",
                careers_url="https://www.capgemini.com/fr-fr/carrieres/")
    base.update(kw)
    return Employer(**base)


ONE = {
    "id": "309247-fr_FR_SAPBTP",
    "title": "Ingénieur Traitement du Signal Radar",
    "brand": "Capgemini Engineering",
    "contract_type": "CDI",
    "country_code": "fr-fr",
    "location": "Blagnac, Toulouse, Lyon",
    "experience_level": "Jeunes diplômés",
    "apply_job_url": "https://careers.capgemini.com/job/Blagnac-Ingenieur",
    "description_stripped": "Vous travaillerez sur le traitement du signal "
                            "radar et les algorithmes de poursuite." * 4,
}


def test_the_filters_are_read_off_the_endpoint():
    country, brand = capgemini.parse_endpoint("fr-fr|Capgemini Engineering")
    assert (country, brand) == ("fr-fr", "Capgemini Engineering")


def test_an_endpoint_without_a_brand_polls_the_whole_country():
    assert capgemini.parse_endpoint("fr-fr") == ("fr-fr", "")


def test_a_posting_keeps_its_own_apply_url():
    posting = capgemini.parse_jobs({"data": [ONE]}, employer())[0]
    assert posting.url == "https://careers.capgemini.com/job/Blagnac-Ingenieur"
    assert posting.external_id == "309247-fr_FR_SAPBTP"


def test_only_the_first_of_several_cities_is_kept():
    """One posting listing four sites is still one job; the extra cities do
    not change whether it is worth reading."""
    posting = capgemini.parse_jobs({"data": [ONE]}, employer())[0]
    assert posting.city == "Blagnac"


def test_the_country_code_becomes_an_iso_code():
    posting = capgemini.parse_jobs({"data": [ONE]}, employer())[0]
    assert posting.country == "FR"


def test_the_full_posting_text_is_carried_through():
    """This is what makes the source worth having: no second fetch before the
    model can read it."""
    posting = capgemini.parse_jobs({"data": [ONE]}, employer())[0]
    assert "algorithmes de poursuite" in posting.description
    assert len(posting.description) > 200


def test_an_empty_board_is_not_an_error():
    assert capgemini.parse_jobs({"data": []}, employer()) == []
    assert capgemini.parse_jobs({}, employer()) == []


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

    def get(self, url, params=None):
        self.calls.append(params or {})
        page = int((params or {}).get("page", 1))
        start = (page - 1) * self.PAGE
        data = [dict(ONE, id=str(start + i))
                for i in range(min(self.PAGE, max(0, self.total - start)))]
        return FakeResponse({"count": self.total, "data": data})


def test_fetch_sends_the_configured_filters():
    client = FakeClient()
    capgemini.fetch(employer(), client, page_size=FakeClient.PAGE)
    assert client.calls[0]["country_code"] == "fr-fr"
    assert client.calls[0]["brand"] == "Capgemini Engineering"


def test_fetch_walks_the_pages():
    client = FakeClient(total=5)
    assert len(capgemini.fetch(employer(), client, page_size=FakeClient.PAGE)) == 5
    assert [c["page"] for c in client.calls] == [1, 2, 3]


def test_fetch_stops_at_the_page_cap():
    client = FakeClient(total=1000)
    postings = capgemini.fetch(employer(), client, page_size=FakeClient.PAGE,
                               max_pages=2)
    assert len(postings) == 4


def test_fetch_without_an_endpoint_asks_for_nothing():
    client = FakeClient()
    assert capgemini.fetch(employer(ats_endpoint=""), client) == []
    assert client.calls == []
