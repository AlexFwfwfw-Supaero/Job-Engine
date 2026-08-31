"""Cornerstone OnDemand career sites (GMV, OHB).

GMV is the largest GNSS house in the watchlist, and its board sat unpolled
because the API answers 401 without an Authorization header. The header is an
anonymous JWT the career-site page hands to every visitor — no login, no
credential, the same token a browser gets.
"""

import json

import pytest

from jobhunt.models import Employer
from jobhunt.sources import cornerstone as cs

HOME = "https://gmv.csod.com/ux/ats/careersite/4/home?c=gmv&lang=en-US"


def employer(**kw):
    base = dict(name="GMV", ats="cornerstone", ats_endpoint=HOME, country="ES",
                careers_url=HOME)
    base.update(kw)
    return Employer(**base)


def test_the_site_details_are_read_off_the_endpoint_url():
    site = cs.parse_endpoint(HOME)
    assert site.host == "gmv.csod.com"
    assert site.corp == "gmv"
    assert site.site_id == 4


def test_an_endpoint_that_is_not_a_career_site_is_rejected():
    with pytest.raises(ValueError):
        cs.parse_endpoint("https://gmv.csod.com/")


def test_the_token_is_taken_from_the_page_the_browser_gets():
    page = ('<script>if(!csod.context) csod.context='
            '{"corp":"gmv","token":"abc.def.ghi","version":"1.25.6"};</script>')
    assert cs.token_from_page(page) == "abc.def.ghi"


def test_a_page_without_a_token_is_an_error_not_a_silent_empty_poll():
    with pytest.raises(ValueError):
        cs.token_from_page("<html>maintenance</html>")


REQ = {
    "requisitionId": 4944,
    "displayJobTitle": "GNSS Systems Engineer",
    "postingEffectiveDate": "7/27/2026",
    "locations": [{"city": "Tres Cantos", "state": "Madrid", "country": "ES"}],
}


def payload(requisitions, total=1):
    return {"status": 1, "data": {"totalCount": total,
                                  "requisitions": requisitions}}


def test_a_requisition_becomes_an_applyable_url():
    posting = cs.parse_jobs(payload([REQ]), employer())[0]
    assert posting.url == (
        "https://gmv.csod.com/ux/ats/careersite/4/home/requisition/4944?c=gmv"
    )
    assert posting.title == "GNSS Systems Engineer"
    assert posting.external_id == "4944"


def test_the_country_comes_from_the_posting():
    """GMV hires in Madrid, Warsaw, Bogota and Kuala Lumpur off one board."""
    entry = dict(REQ, locations=[{"city": "Bogotá", "country": "CO"}])
    posting = cs.parse_jobs(payload([entry]), employer(country="ES"))[0]
    assert (posting.city, posting.country) == ("Bogotá", "CO")


def test_a_requisition_with_no_location_falls_back_to_the_employer():
    posting = cs.parse_jobs(payload([dict(REQ, locations=[])]), employer())[0]
    assert posting.country == "ES"


def test_an_empty_board_is_not_an_error():
    assert cs.parse_jobs(payload([], total=0), employer()) == []


# --- the request itself -------------------------------------------------

class FakeResponse:
    def __init__(self, payload=None, text=""):
        self._payload, self.text = payload, text

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    """Serves the token page, then pages of requisitions."""

    PAGE = 2

    def __init__(self, total=5):
        self.total = total
        self.posts = []

    def get(self, url, **kw):
        return FakeResponse(text='csod.context={"token":"tok.en.jwt"};')

    def post(self, url, json=None, params=None, headers=None):
        self.posts.append({"json": json, "headers": headers, "params": params})
        page = json["pageNumber"]
        start = (page - 1) * self.PAGE
        items = [dict(REQ, requisitionId=start + i)
                 for i in range(min(self.PAGE, max(0, self.total - start)))]
        return FakeResponse(payload=payload(items, total=self.total))


def test_fetch_sends_the_token_as_a_bearer_header():
    client = FakeClient()
    cs.fetch(employer(), client, page_size=FakeClient.PAGE)
    assert client.posts[0]["headers"]["Authorization"] == "Bearer tok.en.jwt"


def test_fetch_asks_for_the_culture_the_api_requires():
    """The API rejects a search with no cultureName, which is how this was
    silently returning nothing before."""
    client = FakeClient()
    cs.fetch(employer(), client, page_size=FakeClient.PAGE)
    assert client.posts[0]["json"]["cultureName"]


def test_fetch_walks_the_pages_until_the_board_runs_out():
    client = FakeClient(total=5)
    postings = cs.fetch(employer(), client, page_size=FakeClient.PAGE)
    assert len(postings) == 5
    assert [p["json"]["pageNumber"] for p in client.posts] == [1, 2, 3]


def test_fetch_stops_at_the_page_cap():
    client = FakeClient(total=1000)
    postings = cs.fetch(employer(), client, page_size=FakeClient.PAGE, max_pages=2)
    assert len(postings) == 4


def test_fetch_without_an_endpoint_asks_for_nothing():
    client = FakeClient()
    assert cs.fetch(employer(ats_endpoint=""), client) == []
    assert client.posts == []


# --- reading one posting's advert --------------------------------------

def test_job_details_url_is_built_from_the_browsable_url():
    """The advert is not in the page: Cornerstone renders it in JavaScript,
    exactly like Workday, so the stored URL fetches 20k of chrome and no
    description. The requisition id in that URL is what the detail service
    wants."""
    from jobhunt.sources.cornerstone import job_details_url

    assert job_details_url(
        "https://career-ohb.csod.com/ux/ats/careersite/4/home/requisition/8433"
        "?c=career-ohb"
    ) == ("https://career-ohb.csod.com/services/x/job-requisition/v2/"
          "requisitions/8433/jobDetails?cultureId=1")


def test_job_details_url_returns_none_for_another_source():
    from jobhunt.sources.cornerstone import job_details_url

    assert job_details_url("https://boards.greenhouse.io/x/jobs/1") is None


class _Response:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _Client:
    """Answers the home page with a token, then the detail service."""

    def __init__(self, detail):
        self.detail = detail
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if "/jobDetails" in url:
            return _Response(payload=self.detail)
        return _Response(text='csod.context = {"token":"anon-jwt"};')


def test_posting_text_reads_the_advert_out_of_the_detail_service():
    from jobhunt.sources.cornerstone import posting_text

    client = _Client({"data": {
        "externalDescription": "<ul><li>AOCS &amp; GNC algorithms</li></ul>",
        "displayTitle": "AOCS & GNC Engineer",
    }})
    text = posting_text(
        "https://career-ohb.csod.com/ux/ats/careersite/4/home/requisition/8433"
        "?c=career-ohb", client)

    assert "AOCS & GNC algorithms" in text
    assert "<" not in text, "markup is stripped for the model"


def test_posting_text_is_authorised_with_the_anonymous_token():
    """Same anonymous JWT the search call needs; without it this 401s."""
    from jobhunt.sources.cornerstone import posting_text

    client = _Client({"data": {"externalDescription": "Navigation systems work"}})
    posting_text(
        "https://career-ohb.csod.com/ux/ats/careersite/4/home/requisition/8433"
        "?c=career-ohb", client)

    assert any("/home?c=career-ohb" in u for u in client.urls), "token is fetched"
    assert any("/jobDetails" in u for u in client.urls)
