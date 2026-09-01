import textwrap

import pytest

from jobhunt.config import (
    load_cities, load_comp, load_deadlines, load_employers, load_scoring,
)


def write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return p


def test_load_scoring(tmp_path):
    p = write(tmp_path, "scoring.yaml", """
        weights: {comp: 0.3, qol: 0.2, fit: 0.5}
        qol_weights: {sunshine: 0.5, nature: 0.3, rent: 0.2}
        sunshine_range: [1300, 2900]
        rent_range: [400, 1800]
        excluded_countries: [GB]
        known_languages: [en, fr, es, pt]
        language_keywords:
          de: ["german", "deutsch"]
        negative_keywords: ["land surveyor", "sales"]
        staleness:
          applied: [14, 30]
          phd: [21, 45]
        role_families:
          - name: gnss
            weight: 1.0
            keywords: ["gnss", "galileo", "receiver"]
          - name: radar
            weight: 0.7
            keywords: ["radar"]
    """)
    cfg = load_scoring(p)
    assert cfg.weights["fit"] == 0.5
    assert cfg.excluded_countries == ["GB"]
    assert cfg.staleness["applied"] == [14, 30]
    assert cfg.role_families[0].name == "gnss"
    assert "galileo" in cfg.role_families[0].keywords
    assert cfg.role_families[1].weight == 0.7


def test_scoring_weights_must_be_present(tmp_path):
    p = write(tmp_path, "scoring.yaml", "weights: {comp: 0.5}\n")
    with pytest.raises(ValueError, match="weights"):
        load_scoring(p)


def test_load_cities_keys_are_lowercased(tmp_path):
    p = write(tmp_path, "cities.yaml", """
        cities:
          - name: Munich
            country: DE
            sunshine_hours: 1777
            nature: 9
            rent_index: 1400
          - name: Toulouse
            country: FR
            sunshine_hours: 2100
            nature: 7
            rent_index: 750
    """)
    cities = load_cities(p)
    assert cities["munich"].sunshine_hours == 1777
    assert cities["toulouse"].country == "FR"


def test_load_comp(tmp_path):
    p = write(tmp_path, "comp.yaml", """
        reference_purchasing_power: 40000
        effective_tax: {DE: 0.36, FR: 0.28}
        pli: {DE: 1.07, FR: 1.03}
        salary_by_country:
          DE: {junior: 58000, phd: 33000}
          FR: {junior: 41000, phd: 24000}
    """)
    cfg = load_comp(p)
    assert cfg.salary_by_country["DE"]["junior"] == 58000
    assert cfg.effective_tax["FR"] == 0.28
    assert cfg.reference_purchasing_power == 40000


def test_load_employers(tmp_path):
    p = write(tmp_path, "employers.yaml", """
        employers:
          - name: Septentrio
            country: BE
            city: Leuven
            ats: manual
            careers_url: https://example.com/careers
            tags: [gnss]
    """)
    employers = load_employers(p)
    assert employers[0].name == "Septentrio"
    assert employers[0].tags == ["gnss"]
    assert employers[0].poll_enabled is False


def test_load_deadlines(tmp_path):
    p = write(tmp_path, "deadlines.yaml", """
        deadlines:
          - name: ESA Young Graduate Trainee
            employer: ESA
            opens: 2026-09-01
            closes: 2026-11-15
            url: https://example.com/ygt
            lead_days: 60
    """)
    d = load_deadlines(p)
    assert d[0].name == "ESA Young Graduate Trainee"
    assert d[0].closes == "2026-11-15"
    assert d[0].lead_days == 60


def test_the_search_vocabulary_asks_for_doctoral_work():
    """Workday searches its own text server-side, so a term absent from this
    list is a posting that is never retrieved at all. Airbus, Thales and
    ArianeGroup all advertise funded theses; every doctoral posting in the
    database had been found by accident, on a domain word that happened to be
    in the title."""
    from pathlib import Path

    from jobhunt.config import load_scoring

    terms = {t.lower() for t in load_scoring(Path("config/scoring.yaml")).poll_search_terms}
    for wanted in ("phd", "doktorand", "thèse", "cifre", "doctorant"):
        assert any(wanted in t for t in terms), wanted
