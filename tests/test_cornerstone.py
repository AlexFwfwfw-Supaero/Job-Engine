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
