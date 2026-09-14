from pathlib import Path

import pytest

from jobhunt.models import Employer
from jobhunt.sources import isae_theses

FIXTURE = Path(__file__).parent / "fixtures" / "isae_phd_offers.html"


@pytest.fixture
def employer():
    return Employer(name="ISAE-SUPAERO", country="FR", city="Toulouse",
                    ats="isae_theses",
                    ats_endpoint="https://www.isae-supaero.fr/en/type-contrat/phd-offer/",
                    careers_url="https://www.isae-supaero.fr/en/type-contrat/phd-offer/")


class FakePage:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return FakePage(self.text)


def test_every_offer_on_the_archive_is_returned(employer):
    """The archive inlines each offer in full rather than linking to it, so
    one fetch carries every subject and its body."""
    postings = isae_theses.parse_offers(
        FIXTURE.read_text(encoding="utf-8"), employer)
    assert len(postings) == 2
    assert postings[0].title == (
        "Deterministic QoS Guarantees in Next -Generation LEO Satellite "
        "Constellations")


def test_the_offer_is_stored_at_doctoral_level(employer):
    """Priced as a graduate job, a thesis reads as a derisory salary. The
    contract type is on the card, so nothing has to be inferred from wording."""
    postings = isae_theses.parse_offers(
        FIXTURE.read_text(encoding="utf-8"), employer)
    assert all(p.level == "phd" for p in postings)


def test_the_body_is_carried_so_nothing_needs_a_second_fetch(employer):
    """A title alone would put "Deterministic QoS Guarantees" outside every
    role family; the body is where the navigation content is."""
    postings = isae_theses.parse_offers(
        FIXTURE.read_text(encoding="utf-8"), employer)
    assert len(postings[0].description) > 200


def test_the_pdf_is_the_application_route(employer):
    """For a doctoral offer the PDF is the subject description and the contact
    route, the way the supervisor's address is for ONERA."""
    postings = isae_theses.parse_offers(
        FIXTURE.read_text(encoding="utf-8"), employer)
    assert postings[0].url.endswith(".pdf")
    assert "LEO-Satellite-Constellations" in postings[0].url


def test_the_city_is_the_employers_since_the_card_states_none(employer):
    postings = isae_theses.parse_offers(
        FIXTURE.read_text(encoding="utf-8"), employer)
    assert postings[0].city == "Toulouse"
    assert postings[0].country == "FR"


def test_a_block_without_a_title_is_skipped(employer):
    assert isae_theses.parse_offers("<div>no offers here</div>", employer) == []


def test_fetch_reads_the_archive_once(employer):
    client = FakeClient(FIXTURE.read_text(encoding="utf-8"))
    postings = isae_theses.fetch(employer, client)
    assert len(client.urls) == 1
    assert len(postings) == 2


def test_the_registry_knows_the_source():
    from jobhunt.poll import SOURCE_REGISTRY

    assert SOURCE_REGISTRY["isae_theses"] is isae_theses.fetch
