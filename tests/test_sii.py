"""SII Group's Drupal careers site.

No API, but unlike most consultancy sites the listing is rendered server-side,
so the postings are in the HTML a plain fetch already returns. Parsing that is
reading a public page — the alternative was leaving a French aerospace
consultancy invisible.
"""

from jobhunt.models import Employer
from jobhunt.sources import sii

ROW = """
<div class="views-row"><div class="item-slider-offre-emp">
 <h4><div class="field field--name-field-contract field--type-list-string">
   <div class="field__item">CDI</div></div></h4>
 <a href="/fr-FR/node/22691"><h3>Ingénieur Traitement du Signal</h3></a>
 <div class="field field--name-field-job field--type-entity-reference">
   <div class="field__item">Systems</div></div>
 <div class="field field--name-field-profile field--type-entity-reference">
   <div class="field__item">Aeronautics &amp; Space</div></div>
 <div class="field field--name-field-location field--type-entity-reference">
   <div class="field__item">Toulouse, France</div></div>
</div></div>
"""


def employer(**kw):
    base = dict(name="SII Group", ats="sii",
                ats_endpoint="https://sii-group.com/fr-FR/join-us",
                country="FR", careers_url="https://sii-group.com/fr-FR/join-us")
    base.update(kw)
    return Employer(**base)


def test_a_row_becomes_a_posting_with_an_absolute_url():
    posting = sii.parse_jobs(ROW, employer())[0]
    assert posting.url == "https://sii-group.com/fr-FR/node/22691"
    assert posting.title == "Ingénieur Traitement du Signal"
    assert posting.external_id == "22691"


def test_the_city_and_country_are_split_out_of_the_location():
    posting = sii.parse_jobs(ROW, employer())[0]
    assert (posting.city, posting.country) == ("Toulouse", "FR")


def test_a_foreign_posting_is_not_filed_under_the_headquarters():
    """SII advertises across Europe off one board; Prague is not France."""
    row = ROW.replace("Toulouse, France", "Prague, Czech Republic")
    assert sii.parse_jobs(row, employer())[0].country == "CZ"


def test_an_unmappable_country_falls_back_to_the_employer():
    row = ROW.replace("Toulouse, France", "Somewhere, Atlantis")
    assert sii.parse_jobs(row, employer())[0].country == "FR"


def test_the_contract_and_the_field_reach_the_matcher():
    """The title alone is thin. Everything else the row carries is signal."""
    posting = sii.parse_jobs(ROW, employer())[0]
    assert "Aeronautics" in posting.description
    assert "CDI" in posting.description


def test_html_entities_are_decoded():
    posting = sii.parse_jobs(ROW, employer())[0]
    assert "&amp;" not in posting.description


def test_several_rows_all_come_back():
    assert len(sii.parse_jobs(ROW * 3, employer())) == 3


def test_a_page_with_no_rows_is_not_an_error():
    assert sii.parse_jobs("<html><body>rien</body></html>", employer()) == []


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, pages=2):
        self.pages, self.calls = pages, []

    def get(self, url, params=None):
        self.calls.append(params or {})
        page = int((params or {}).get("page", 0))
        return FakeResponse(ROW if page < self.pages else "<html></html>")


def test_fetch_walks_pages_until_one_comes_back_empty():
    client = FakeClient(pages=2)
    postings = sii.fetch(employer(), client)
    assert len(postings) == 2
    assert [c["page"] for c in client.calls] == [0, 1, 2]


def test_fetch_stops_at_the_page_cap():
    client = FakeClient(pages=99)
    assert len(sii.fetch(employer(), client, max_pages=3)) == 3


def test_fetch_without_an_endpoint_asks_for_nothing():
    client = FakeClient()
    assert sii.fetch(employer(ats_endpoint=""), client) == []
    assert client.calls == []
