"""ONERA's doctoral offers, listed per department.

The fixtures below keep the live page's exact tag spacing (`<td  class=`, two
spaces). An earlier fixture tidied it to one, and every test passed against a
parser that returned nothing at all from the real site.

These are not in the TalentLink feed the ONERA employer entry polls; they live
on five Drupal pages under w3.onera.fr, one per department. DEMR
(electromagnetics and radar) and DTIS (information processing and systems) are
the two that matter here.

The trap is that each page lists BOTH the filled positions and the open ones,
under separate "Pourvu" and "A pourvoir" headings — and the filled block comes
first. A parser that takes every table row on the page returns mostly closed
positions, and returns them looking exactly like open ones.
"""

from jobhunt.models import Employer
from jobhunt.sources import onera_theses as ot

FILLED_ROW = """
<tr class="odd views-row-first">
 <td  class="views-field views-field-title"> PHY-DEMR-2026-06 </td>
 <td  class="views-field views-field-body"> Méthodes CDO pour les équations de
   Maxwell temporelles<p>contact&nbsp;:&nbsp;</p> </td>
 <td  class="views-field views-field-field-centre">
   <a href="http://www.onera.fr/centres/toulouse">Toulouse</a> </td>
</tr>
"""

OPEN_ROW = """
<tr class="odd views-row-first views-row-last">
 <td  class="views-field views-field-title">
   <a href="/formationparlarecherche/meta-surfaces-antennes">PHY-DEMR-2026-02</a> </td>
 <td  class="views-field views-field-body"> Etude et conception de méta surfaces
   couplées à des antennes pour la réduction de signature radar
   <p>contact&nbsp;:&nbsp;andre.barka@onera.fr</p> </td>
 <td  class="views-field views-field-field-centre">
   <a href="http://www.onera.fr/centres/palaiseau">Palaiseau</a> </td>
 <td  class="views-field views-field-field-fichier-joint">
   <a href="https://w3.onera.fr/files/phy-demr-2026-02.pdf">pdf</a> </td>
 <td  class="views-field views-field-field-lien-de-candidature-demr">
   <a href="https://emea3.recruitmentplatform.com/apply-app/pages/application-form?jobId=X-6028"></a> </td>
</tr>
"""


def page(filled=FILLED_ROW, open_rows=OPEN_ROW):
    return f"""<html><body>
 <div class="block"><h2 class="title">Pourvu</h2>
  <table class="views-table cols-3"><tbody>{filled}</tbody></table></div>
 <div class="block"><h2 class="title">A pourvoir</h2>
  <table class="views-table cols-5"><tbody>{open_rows}</tbody></table></div>
 </body></html>"""


def employer(**kw):
    base = dict(name="ONERA Doctoral", ats="onera_theses", country="FR",
                ats_endpoint="https://w3.onera.fr/formationparlarecherche/theses-demr",
                careers_url="https://w3.onera.fr/formationparlarecherche/")
    base.update(kw)
    return Employer(**base)


# --- the thing that matters ---------------------------------------------

def test_only_the_open_positions_are_returned():
    postings = ot.parse_jobs(page(), employer())
    assert len(postings) == 1
    assert "méta surfaces" in postings[0].title


def test_a_filled_position_is_never_returned_even_though_it_comes_first():
    """'Pourvu' is the first block on every one of these pages."""
    titles = [p.title for p in ot.parse_jobs(page(), employer())]
    assert not any("Maxwell" in t for t in titles)


def test_a_department_with_nothing_open_returns_nothing():
    html = page().replace('<h2 class="title">A pourvoir</h2>', '<h2 class="title">Autre</h2>')
    assert ot.parse_jobs(html, employer()) == []


def test_the_accented_heading_is_recognised_too():
    """The site is inconsistent about the accent on À."""
    html = page().replace("A pourvoir", "À pourvoir")
    assert len(ot.parse_jobs(html, employer())) == 1


def test_a_page_with_no_blocks_at_all_is_not_an_error():
    assert ot.parse_jobs("<html><body>rien</body></html>", employer()) == []


# --- the rest of the row ------------------------------------------------

def test_the_subject_becomes_the_title_not_the_reference():
    """'PHY-DEMR-2026-02' tells the matcher nothing; the subject line is
    where the words radar and antennes are."""
    posting = ot.parse_jobs(page(), employer())[0]
    assert posting.title.startswith("Etude et conception")
    assert posting.external_id == "PHY-DEMR-2026-02"


def test_the_detail_page_is_the_url():
    posting = ot.parse_jobs(page(), employer())[0]
    assert posting.url == (
        "https://w3.onera.fr/formationparlarecherche/meta-surfaces-antennes")


def test_a_row_with_no_detail_link_still_gets_a_unique_url():
    """Two theses must never collide on one URL: the store keys on it."""
    row = OPEN_ROW.replace(
        '<a href="/formationparlarecherche/meta-surfaces-antennes">PHY-DEMR-2026-02</a>',
        "PHY-DEMR-2026-02")
    posting = ot.parse_jobs(page(open_rows=row), employer())[0]
    assert posting.url.endswith("theses-demr#PHY-DEMR-2026-02")


def test_the_centre_becomes_the_city():
    assert ot.parse_jobs(page(), employer())[0].city == "Palaiseau"


def test_the_supervisors_address_is_kept():
    """For a PhD the contact address is the application route."""
    assert "andre.barka@onera.fr" in ot.parse_jobs(page(), employer())[0].description


def test_a_thesis_is_stored_as_a_phd_not_a_junior_post():
    assert ot.parse_jobs(page(), employer())[0].level == "phd"


# --- fetching several department pages ----------------------------------

class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return FakeResponse(page())


def test_fetch_reads_every_configured_department():
    client = FakeClient()
    e = employer(ats_endpoint=(
        "https://w3.onera.fr/formationparlarecherche/theses-demr,"
        "https://w3.onera.fr/formationparlarecherche/theses-dtis"))
    postings = ot.fetch(e, client)
    assert len(client.urls) == 2
    assert len(postings) == 2


def test_one_dead_department_does_not_lose_the_others():
    """Five pages, and one 500 must not look like ONERA having no theses."""
    class Flaky(FakeClient):
        def get(self, url):
            self.urls.append(url)
            if "dtis" in url:
                raise ConnectionError("gone")
            return FakeResponse(page())

    client = Flaky()
    e = employer(ats_endpoint=(
        "https://w3.onera.fr/formationparlarecherche/theses-dtis,"
        "https://w3.onera.fr/formationparlarecherche/theses-demr"))
    assert len(ot.fetch(e, client)) == 1


def test_fetch_without_an_endpoint_asks_for_nothing():
    client = FakeClient()
    assert ot.fetch(employer(ats_endpoint=""), client) == []
    assert client.urls == []
