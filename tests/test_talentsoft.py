import re
from pathlib import Path

import pytest

from jobhunt.models import Employer
from jobhunt.sources import talentsoft

FIXTURE = Path(__file__).parent / "fixtures" / "talentsoft_safran.html"


@pytest.fixture
def employer():
    return Employer(name="Safran Data Systems", country="FR", city="Toulouse",
                    ats="talentsoft",
                    ats_endpoint="https://careers.safran-group.com",
                    careers_url="https://careers.safran-group.com/accueil.aspx")


class FakePage:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeClient:
    """Serves the fixture for page 1 and repeats it after that, which is what
    Talentsoft does when you page past the end."""

    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        index = min(len(self.urls) - 1, len(self.pages) - 1)
        return FakePage(self.pages[index])


def test_a_list_page_yields_postings(employer):
    postings = talentsoft.parse_jobs(FIXTURE.read_text(encoding="utf-8"),
                                     employer)
    assert len(postings) == 4
    first = postings[0]
    assert first.title == "Ingénieur Système Centrales de Navigation Export F/H"
    assert first.url == ("https://careers.safran-group.com/offre-de-emploi/"
                         "emploi-ingenieur-systeme-centrales-de-navigation-"
                         "export-f-h_169430.aspx")
    assert first.external_id == "2025-169430"
    assert first.source == "talentsoft"
    assert first.employer_name == "Safran Data Systems"


def test_the_town_is_pulled_out_of_the_postal_address(employer):
    """Rows carry a full address where every other source carries a town, and
    left whole it would never match cities.yaml."""
    postings = talentsoft.parse_jobs(FIXTURE.read_text(encoding="utf-8"),
                                     employer)
    assert postings[0].city == "ERAGNY-SUR-OISE"


@pytest.mark.parametrize("address,expected", [
    ("21 avenue du Gros Chêne 95610 ERAGNY-SUR-OISE", "ERAGNY-SUR-OISE"),
    ("100, avenue de Paris  91344 Massy France", "Massy"),
    ("147  Piazza Arturo Graf 149 10126 TORINO", "TORINO"),
    ("4 Boulevard Gisèle Halimi NANTES", "NANTES"),
    ("Massy", "Massy"),
    ("Gloucester", "Gloucester"),
    ("", ""),
])
def test_city_from_address(address, expected):
    assert talentsoft.city_from_address(address) == expected


def test_the_row_carries_no_advert_so_enrichment_fetches_one(employer):
    """A row scored against its own title is a title matched against itself."""
    postings = talentsoft.parse_jobs(FIXTURE.read_text(encoding="utf-8"),
                                     employer)
    assert all(p.description == "" for p in postings)


def test_fetch_pages_until_a_page_adds_nothing(employer):
    page = FIXTURE.read_text(encoding="utf-8")
    client = FakeClient([page])
    postings = talentsoft.fetch(employer, client, max_pages=10)

    # Page two repeats page one, which is how the board signals the end.
    assert len(client.urls) == 2
    assert "page=1" in client.urls[0] and "page=2" in client.urls[1]
    assert len(postings) == 4


def test_fetch_respects_the_page_cap(employer):
    """The cap is a stop, not an expectation: a poll that quietly saw a fifth
    of the board is the failure it exists to prevent."""
    pages = [FIXTURE.read_text(encoding="utf-8").replace(
        "_169430", f"_16943{n}").replace("_169425", f"_16942{n}")
        for n in range(5)]
    client = FakeClient(pages)
    talentsoft.fetch(employer, client, max_pages=3)
    assert len(client.urls) == 3


def test_a_row_without_a_link_is_skipped(employer):
    broken = '<li class="ts-offer-list-item"><h3>No link here</h3></li>'
    assert talentsoft.parse_jobs(broken, employer) == []


def test_the_registry_knows_the_source():
    from jobhunt.poll import SOURCE_REGISTRY, source_kwargs
    from jobhunt.config import ScoringConfig

    assert SOURCE_REGISTRY["talentsoft"] is talentsoft.fetch
    cfg = ScoringConfig(weights={}, qol_weights={}, role_families=[])
    kwargs = source_kwargs("talentsoft", cfg, {"max_pages": 10})
    assert kwargs["max_pages"] == talentsoft.DEFAULT_MAX_PAGES


# --- walking the board by job family -----------------------------------

FACETS = (
    '<a href="/offre-de-emploi/liste-toutes-offres.aspx?changefacet=1&'
    'facet_JobFamily=4244" title="Affiner ma recherche sur le critère : '
    'Electronique et automatique (25)">Electronique et automatique (25)</a>'
    '<a href="/offre-de-emploi/liste-toutes-offres.aspx?changefacet=1&'
    'facet_JobFamily=4252" title="Affiner ma recherche sur le critère : '
    'Mathematiques et algorithmes (11)">Mathematiques et algorithmes (11)</a>'
)


def test_families_are_read_off_the_page_with_their_counts():
    """Hard-coding Safran's taxonomy would rot into silent under-reporting."""
    assert talentsoft.discover_families(FACETS) == [("4244", 25), ("4252", 11)]


def test_families_are_walked_largest_first_for_exactly_their_page_count():
    """The count is exact, so the last page is known before asking for it:
    25 offers is two pages, 11 is one."""
    page = FIXTURE.read_text(encoding="utf-8") + FACETS
    client = FakeClient([page])
    talentsoft.fetch(employer_for_test(), client, max_pages=50)

    assert client.urls[0].endswith("?all=1&mode=list&page=1")
    families = [re.search(r"facet_JobFamily=(\d+)&mode=list&page=(\d+)", u).groups()
                for u in client.urls[1:]]
    assert families == [("4244", "1"), ("4244", "2"), ("4252", "1")]


class FakeCookies:
    """The session the portal keeps for us: clearing it forgets the facets."""

    def __init__(self, client):
        self.client = client
        self.cleared = 0

    def clear(self):
        self.cleared += 1
        self.client.active = []


class FacetAccumulatingClient:
    """Talentsoft keeps the active facets in the session, and `changefacet=1`
    *adds* one rather than replacing it. A client that carries its cookies
    from one family to the next therefore asks for family B AND family A, and
    the walk collapses into a shrinking intersection — live, that returned
    1133 of Safran's 3805 offers and reported no error at all.
    """

    def __init__(self, page):
        self.page = page
        self.active: list[str] = []
        self.cookies = FakeCookies(self)
        self.urls: list[str] = []

    def get(self, url):
        self.urls.append(url)
        found = re.search(r"facet_JobFamily=(\d+)", url)
        if found and found.group(1) not in self.active:
            self.active.append(found.group(1))
        # More than one facet at once is the bug: the server would answer with
        # an intersection, so serve nothing and let the assertion catch it.
        return FakePage(self.page if len(self.active) <= 1 else "")


def test_each_family_is_asked_for_on_its_own_session():
    """Without clearing the session between families, every family after the
    first is filtered by its predecessors too."""
    page = FIXTURE.read_text(encoding="utf-8") + FACETS
    client = FacetAccumulatingClient(page)
    talentsoft.fetch(employer_for_test(), client, max_pages=50)

    assert client.cookies.cleared >= 2, "one session reset per family"
    assert client.active == ["4252"], f"facets leaked: {client.active}"


def test_the_page_budget_is_shared_across_families():
    page = FIXTURE.read_text(encoding="utf-8") + FACETS
    client = FakeClient([page])
    talentsoft.fetch(employer_for_test(), client, max_pages=3)
    assert len(client.urls) == 3


def test_a_portal_with_no_families_pages_the_flat_listing():
    """The fallback, and what the 50-page wrap makes incomplete — which is why
    the family walk exists."""
    page = FIXTURE.read_text(encoding="utf-8")
    client = FakeClient([page])
    talentsoft.fetch(employer_for_test(), client, max_pages=10)
    assert all("facet_JobFamily" not in u for u in client.urls)
    assert len(client.urls) == 2  # page 2 repeats page 1, so it stops


def employer_for_test():
    return Employer(name="Safran Data Systems", country="FR", city="Toulouse",
                    ats="talentsoft",
                    ats_endpoint="https://careers.safran-group.com",
                    careers_url="https://careers.safran-group.com/accueil.aspx")


# --- the country the address states ------------------------------------

@pytest.mark.parametrize("address,expected", [
    # The country is named outright at the end, which is what the board does
    # for most of its non-French sites.
    ("Rue de Trois Fontaines HERSTAL Belgium", "BE"),
    ("2 Fanshawe Road Banbury United Kingdom", "GB"),
    ("Am Kappengraben 6 Herborn Germany", "DE"),
    ("Cra. 9 Chihuahua Mexico", "MX"),
    ("1 Marsh Street Botany Australia", "AU"),
    # Written in French, because the French portal writes some of them so.
    ("Bahnhofstrasse Murr Allemagne", "DE"),
    ("Via Roma TORINO Italie", "IT"),
    ("Calle Real Granada Espagne", "ES"),
    # A US state code before the postcode is the only country marker there is.
    ("7330 Lincoln Way CA 92841 Garden Grove", "US"),
    ("1 Bell Road WA 98052 Redmond", "US"),
    # France is stated on plenty of rows and must still read as France.
    ("100, avenue de Paris 91344 Massy France", "FR"),
    # Nothing states a country: the caller keeps its default rather than
    # guessing from a town name that could be in three countries.
    ("21 avenue du Gros Chêne 95610 ERAGNY-SUR-OISE", ""),
    ("Massy", ""),
    ("", ""),
])
def test_country_from_address(address, expected):
    assert talentsoft.country_from_address(address) == expected


def test_a_stated_country_is_believed_over_the_employers_own(employer):
    """Safran's board is group-wide. Every row used to be stamped FR, which put
    sixty postings in Sydney, Bangalore and Redmond into the results as French
    jobs — and the eight live ones reached the pipeline behind the country
    exclusion that exists to keep them out."""
    row = ('<li class="ts-offer-list-item" data-title="PNT Engineer" '
           'data-reference="2025-172231">'
           '<a class="ts-offer-list-item__title-link" href="/x.aspx">x</a>'
           '<ul class="ts-offer-list-item__description">'
           '<li>ref</li><li>date</li><li>Permanent</li>'
           '<li>1 Marsh Street Botany Australia</li></ul></li>')
    posting = talentsoft.parse_jobs(row, employer)[0]
    assert posting.country == "AU"
    assert posting.city == "Botany"


def test_an_address_with_no_country_keeps_the_employers_own(employer):
    """Most of the board really is French and says nothing about it."""
    postings = talentsoft.parse_jobs(FIXTURE.read_text(encoding="utf-8"),
                                     employer)
    assert postings[0].country == "FR"


def test_the_country_word_is_not_left_in_the_city(employer):
    """'Botany Australia' as a city matches nothing in cities.yaml."""
    assert talentsoft.city_from_address("1 Marsh Street Botany Australia") \
        == "Botany"
    assert talentsoft.city_from_address("Am Kappengraben 6 Herborn Germany") \
        == "Herborn"


@pytest.mark.parametrize("address,expected", [
    # UK and Canadian postcodes are alphanumeric, so the digits-only hinge
    # never found them and the whole postcode stayed in the town.
    ("Chalker Way OX16 4X Banbury United Kingdom", "Banbury"),
    ("Llantarnam Industrial Park NP44 3HQ Cwmbran United Kingdom", "Cwmbran"),
    ("Trans-Canada Hwy QC H9J 3K1 Kirkland Canada", "Kirkland"),
    ("Westfield Road LU7 9RH Pitstone, Buckinghamshire United Kingdom",
     "Pitstone, Buckinghamshire"),
    # The French hinge still wins where it applies.
    ("21 avenue du Gros Chêne 95610 ERAGNY-SUR-OISE", "ERAGNY-SUR-OISE"),
])
def test_town_after_an_alphanumeric_postcode(address, expected):
    assert talentsoft.city_from_address(address) == expected
