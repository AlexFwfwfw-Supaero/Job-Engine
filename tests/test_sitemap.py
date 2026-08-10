from pathlib import Path

import pytest

from jobhunt.models import Employer
from jobhunt.sources import sitemap

FIXTURES = Path(__file__).parent / "fixtures"
CNES = FIXTURES / "sitemap_cnes.xml"
SIRIUS_INDEX = FIXTURES / "sitemap_sirius_index.xml"
SIRIUS_JOBS = FIXTURES / "sitemap_sirius_jobs.xml"


class FakePage:
    def __init__(self, text, status=200):
        self.text = text
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class FakeClient:
    """Serves whichever fixture the URL asks for, and records the asking."""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url):
        self.requested.append(url)
        if url not in self.pages:
            return FakePage("", status=404)
        return FakePage(self.pages[url])


@pytest.fixture
def cnes():
    return Employer(
        name="CNES", country="FR", city="Toulouse", ats="sitemap",
        ats_endpoint="https://recrutement.cnes.fr/sitemap.xml#/fr/annonce/",
        careers_url="https://recrutement.cnes.fr/en/annonces")


@pytest.fixture
def sirius():
    return Employer(
        name="Sirius Space Services", country="FR", ats="sitemap",
        ats_endpoint=("https://www.sirius-space.com/sitemap.xml"
                      "#/les-offres-sirius-en/"),
        careers_url="https://www.sirius-space.com/careers")


# --- slug reading -----------------------------------------------------------

def test_title_drops_the_leading_advert_id():
    url = "https://careers.telespazio.fr/jobs/7675506-ingenieur-logiciel-operations-spatiales"
    assert sitemap.title_from_url(url) == "Ingenieur logiciel operations spatiales"


def test_title_keeps_a_number_that_is_part_of_the_name():
    # Short runs are not ids: "5" here is the launch vehicle.
    url = "https://x.test/jobs/miura-5-avionics-integration-manager"
    assert sitemap.title_from_url(url) == "Miura 5 avionics integration manager"


@pytest.mark.parametrize("slug,expected", [
    ("8104417-ingenieur-systemes-spatiales-f-h", "Ingenieur systemes spatiales"),
    ("4511233-ingenieur-cybersecurite-hf", "Ingenieur cybersecurite"),
    ("4493823-ingenieur-observabilite-fh", "Ingenieur observabilite"),
    ("systemingenieur-navigation-m-w-d", "Systemingenieur navigation"),
])
def test_title_drops_gender_markers(slug, expected):
    assert sitemap.title_from_url(f"https://x.test/jobs/{slug}") == expected


def test_title_keeps_a_word_that_merely_ends_in_those_letters():
    # "Hardware" must survive a rule aimed at "-h-f".
    url = "https://x.test/jobs/gnss-receiver-hardware"
    assert sitemap.title_from_url(url) == "Gnss receiver hardware"


def test_title_decodes_percent_escapes_without_splitting_the_path():
    # Sirius writes the f/h marker as %2F. Unquoting before splitting the path
    # would turn one advert into two segments and lose half the title.
    url = "https://www.sirius-space.com/les-offres-sirius-en/ing%C3%A9nieur-expert-guidage-f%2Fh"
    assert sitemap.title_from_url(url) == "Ingénieur expert guidage"


def test_title_drops_the_file_extension():
    url = "https://www.pldspace.com/posiciones-abiertas/propulsion-analysis-engineer.html"
    assert sitemap.title_from_url(url) == "Propulsion analysis engineer"


def test_title_capitalises_only_the_first_letter():
    # Title-casing would render this "Gnss Sar Gnc" and misspell every acronym
    # the posting is named after.
    url = "https://x.test/jobs/gnss-and-sar-gnc-engineer"
    assert sitemap.title_from_url(url) == "Gnss and sar gnc engineer"


def test_city_comes_from_a_postcode_anchor():
    url = ("https://recrutement.cnes.fr/fr/annonce/"
           "4511233-ingenieur-cybersecurite-hf-31400-toulouse")
    assert sitemap.city_from_url(url) == "Toulouse"
    assert sitemap.title_from_url(url) == "Ingenieur cybersecurite"


def test_city_is_empty_without_a_postcode_to_anchor_on():
    # "guyanais" is the tail of the title, not a town. Guessing here would
    # file half the board in cities that do not exist.
    url = ("https://recrutement.cnes.fr/fr/annonce/"
           "4459467-developpeur-full-stack-testeur-hf-centre-spatial-guyanais")
    assert sitemap.city_from_url(url) == ""


def test_external_id_is_the_slug_id_when_there_is_one():
    assert sitemap.external_id("https://x.test/jobs/8104417-ingenieur") == "8104417"
    assert sitemap.external_id("https://x.test/jobs/thermal-engineer") == ""


# --- URL filtering ----------------------------------------------------------

def test_the_board_index_itself_is_not_a_job():
    prefix = "/pld-space-empleo/posiciones-abiertas/"
    assert not sitemap.is_job_url(f"https://x.test{prefix}", prefix)
    assert sitemap.is_job_url(f"https://x.test{prefix}welding-engineer.html", prefix)


def test_urls_outside_the_job_path_are_ignored():
    assert not sitemap.is_job_url("https://x.test/privacy-policy", "/jobs/")
    assert not sitemap.is_job_url("https://x.test/people", "/jobs/")


# --- parsing ----------------------------------------------------------------

def test_locations_are_found_whatever_namespace_is_declared():
    xml = ('<urlset xmlns="http://example.invalid/other/schema">'
           "<url><loc>https://x.test/jobs/one</loc></url></urlset>")
    locations, is_index = sitemap.parse_locations(xml, "https://x.test/sitemap.xml")
    assert locations == ["https://x.test/jobs/one"]
    assert is_index is False


def test_malformed_xml_says_where():
    with pytest.raises(ValueError, match="malformed sitemap at https://x.test/s.xml"):
        sitemap.parse_locations("<urlset><url>", "https://x.test/s.xml")


# --- fetch ------------------------------------------------------------------

def test_fetch_reads_a_flat_sitemap(cnes):
    client = FakeClient({
        "https://recrutement.cnes.fr/sitemap.xml": CNES.read_text(encoding="utf-8"),
    })
    postings = sitemap.fetch(cnes, client)

    assert len(postings) == 10
    assert all(p.source == "sitemap" for p in postings)
    assert all(p.employer_name == "CNES" for p in postings)
    # A sitemap says nothing about location, so the employer's country stands.
    assert all(p.country == "FR" for p in postings)
    # And nothing about the advert either — enrichment fetches the page.
    assert all(p.description == "" for p in postings)

    titles = {p.title for p in postings}
    assert ("Piloter des outils de dynamique du vol par ia grace a des "
            "serveurs mcp et n8n") in titles
    assert {p.city for p in postings} >= {"Toulouse", ""}
    # The fragment is a filter, not part of the request.
    assert client.requested == ["https://recrutement.cnes.fr/sitemap.xml"]


def test_fetch_follows_a_sitemap_index(sirius):
    jobs_url = ("https://www.sirius-space.com/dynamic-les-offres-sirius-en"
                "_p_a74085e0_4f4f_405a_8c0d_82d10f95675c_0_5000-sitemap.xml")
    client = FakeClient({
        "https://www.sirius-space.com/sitemap.xml":
            SIRIUS_INDEX.read_text(encoding="utf-8"),
        jobs_url: SIRIUS_JOBS.read_text(encoding="utf-8"),
    })
    postings = sitemap.fetch(sirius, client)

    assert jobs_url in client.requested
    assert "Ingénieur expert guidage" in {p.title for p in postings}


def test_the_fragment_picks_one_half_of_a_two_language_index(sirius):
    """Sirius lists the same jobs twice, French and English. Without the path
    filter every advert would be stored under two URLs and triaged twice."""
    client = FakeClient({
        "https://www.sirius-space.com/sitemap.xml":
            SIRIUS_INDEX.read_text(encoding="utf-8"),
        ("https://www.sirius-space.com/dynamic-les-offres-sirius-en"
         "_p_a74085e0_4f4f_405a_8c0d_82d10f95675c_0_5000-sitemap.xml"):
            SIRIUS_JOBS.read_text(encoding="utf-8"),
    })
    postings = sitemap.fetch(sirius, client)

    assert postings
    assert all("/les-offres-sirius-en/" in p.url for p in postings)
    assert len({p.url for p in postings}) == len(postings)


def test_one_broken_child_sitemap_does_not_lose_the_others():
    employer = Employer(name="Two Boards", country="FR", ats="sitemap",
                        ats_endpoint="https://x.test/sitemap.xml#/jobs/")
    client = FakeClient({
        "https://x.test/sitemap.xml":
            "<sitemapindex><sitemap><loc>https://x.test/gone.xml</loc></sitemap>"
            "<sitemap><loc>https://x.test/good.xml</loc></sitemap></sitemapindex>",
        "https://x.test/good.xml":
            "<urlset><url><loc>https://x.test/jobs/gnc-engineer</loc></url></urlset>",
    })
    postings = sitemap.fetch(employer, client)

    assert [p.title for p in postings] == ["Gnc engineer"]


def test_a_missing_path_fragment_is_an_error_not_a_free_for_all():
    """Without a filter a sitemap is the whole website. Failing loudly puts the
    reason in the poll report; defaulting to 'everything' would put 93
    marketing pages in the pipeline."""
    employer = Employer(name="Exotrail", country="FR", ats="sitemap",
                        ats_endpoint="https://www.exotrail.com/sitemap.xml")
    with pytest.raises(ValueError, match="needs the job path as a fragment"):
        sitemap.fetch(employer, FakeClient({}))


def test_no_endpoint_is_quietly_nothing():
    employer = Employer(name="Nobody", country="FR", ats="sitemap")
    assert sitemap.fetch(employer, FakeClient({})) == []


def test_duplicate_urls_are_stored_once():
    employer = Employer(name="Dupes", country="FR", ats="sitemap",
                        ats_endpoint="https://x.test/sitemap.xml#/jobs/")
    client = FakeClient({"https://x.test/sitemap.xml":
                         "<urlset>"
                         "<url><loc>https://x.test/jobs/gnc-engineer</loc></url>"
                         "<url><loc>https://x.test/jobs/gnc-engineer</loc></url>"
                         "</urlset>"})
    assert len(sitemap.fetch(employer, client)) == 1
