import pytest

from jobhunt.config import RoleFamily, RoleModifiers, ScoringConfig
from jobhunt.match import evaluate


@pytest.fixture
def cfg():
    return ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[
            RoleFamily("gnss", 1.0, ["gnss", "galileo", "receiver"]),
            RoleFamily("radar", 0.8, ["radar", "sar"]),
            RoleFamily("adjacent", 0.5, ["digital signal processing"]),
        ],
        negative_keywords=["land surveyor", "sales representative"],
        excluded_countries=["GB"],
        known_languages=["en", "fr", "es", "pt"],
        language_keywords={"de": ["fluent german"], "nl": ["fluent dutch"]},
    )


def test_excluded_country_is_rejected(cfg):
    r = evaluate("GNSS Engineer", "great job", "GB", cfg)
    assert r.relevant is False
    assert any("GB" in reason for reason in r.reasons)


def test_negative_keyword_in_title_is_rejected(cfg):
    r = evaluate("Land Surveyor", "gnss equipment", "DE", cfg)
    assert r.relevant is False
    assert any("land surveyor" in reason for reason in r.reasons)


def test_negative_keyword_only_in_description_is_not_rejected(cfg):
    r = evaluate("GNSS Engineer", "you will support our sales representative", "DE", cfg)
    assert r.relevant is True


def test_title_match_scores_higher_than_description_match(cfg):
    in_title = evaluate("GNSS Receiver Engineer", "unrelated text", "DE", cfg)
    in_desc = evaluate("Systems Engineer", "work on gnss receiver design", "DE", cfg)
    assert in_title.role_fit > in_desc.role_fit


def test_role_fit_uses_the_best_family_weighted(cfg):
    gnss = evaluate("GNSS Engineer", "", "DE", cfg)
    adjacent = evaluate("Digital Signal Processing Engineer", "", "DE", cfg)
    assert gnss.role_fit > adjacent.role_fit
    assert gnss.matched_families == ["gnss"]


def test_role_fit_is_clamped_to_unit_interval(cfg):
    r = evaluate("GNSS Galileo Receiver Engineer",
                 "gnss galileo receiver gnss galileo receiver", "DE", cfg)
    assert 0.0 <= r.role_fit <= 1.0


def test_no_match_is_irrelevant_but_scored_zero(cfg):
    r = evaluate("Accountant", "bookkeeping", "DE", cfg)
    assert r.role_fit == 0.0
    assert r.relevant is False
    assert any("no role family" in reason for reason in r.reasons)


def test_unknown_language_is_flagged_not_rejected(cfg):
    r = evaluate("GNSS Engineer", "fluent German required", "DE", cfg)
    assert r.relevant is True
    assert r.language_flags == ["de"]


def test_known_language_is_not_flagged(cfg):
    cfg.language_keywords["fr"] = ["fluent french"]
    r = evaluate("GNSS Engineer", "fluent French required", "FR", cfg)
    assert r.language_flags == []


def test_matching_is_case_insensitive(cfg):
    r = evaluate("GALILEO SIGNAL ENGINEER", "", "DE", cfg)
    assert r.role_fit > 0


def test_short_keywords_do_not_match_inside_longer_words(cfg):
    """Regression: 'ins' (inertial nav) matched Installer, Inspecteur, Insurance.

    Found by polling Airbus live — cabin installers outranked GNSS roles.
    """
    cfg.role_families.append(RoleFamily("sensor_fusion", 0.9, ["ins", "imu", "sar"]))
    for title in ["Cabin Installer", "Systems Installer (A320)",
                  "Inspecteur Qualité", "Insurance Analyst", "Sarah's Team Lead"]:
        assert evaluate(title, "", "DE", cfg).role_fit == 0.0, title


def test_short_keywords_still_match_as_whole_words(cfg):
    cfg.role_families.append(RoleFamily("sensor_fusion", 0.9, ["ins", "imu"]))
    assert evaluate("GNSS/INS Integration Engineer", "", "DE", cfg).role_fit > 0
    assert evaluate("Engineer, IMU calibration", "", "DE", cfg).role_fit > 0


def test_multiword_keywords_match_plurals(cfg):
    """Regression: '\\bnavigation system\\b' missed 'Navigation Systems Engineer'.

    Strict boundaries on long keywords silently dropped the single most
    relevant live posting. Boundaries are only needed for short abbreviations.
    """
    cfg.role_families.append(
        RoleFamily("nav", 0.9, ["navigation system", "flight dynamics"])
    )
    assert evaluate("Navigation Systems Engineer", "", "DE", cfg).role_fit > 0
    assert evaluate("Senior Flight Dynamics Engineer", "", "DE", cfg).role_fit > 0


def test_hyphenated_and_multiword_keywords_still_match(cfg):
    cfg.role_families.append(RoleFamily("rf", 0.8, ["anti-jam", "sensor fusion"]))
    assert evaluate("Anti-Jam Antenna Engineer", "", "DE", cfg).role_fit > 0
    assert evaluate("Sensor Fusion Engineer", "", "DE", cfg).role_fit > 0


def test_research_modifier_lifts_a_domain_match(cfg):
    """A GNSS research role should outrank an otherwise identical production one.

    R&D interest is a preference about the *kind* of work, not a separate
    domain, so it adjusts an existing family match rather than standing alone.
    """
    cfg.role_modifiers = RoleModifiers(weight=0.15, keywords=["research", "r&t"])
    plain = evaluate("GNSS Engineer", "", "DE", cfg)
    research = evaluate("GNSS Research Engineer", "", "DE", cfg)
    assert research.role_fit > plain.role_fit
    assert research.modifiers == ["research"]


def test_research_modifier_does_not_rescue_an_irrelevant_role(cfg):
    """'R&D Engineer, cabin materials' must stay at zero.

    Live check: searching Thales for 'r&d' returns 182 jobs led by QA Engineer
    and Service Delivery Manager. A modifier that could score on its own would
    rank those above real GNSS work.
    """
    cfg.role_modifiers = RoleModifiers(weight=0.15, keywords=["research", "r&d"])
    assert evaluate("R&D Engineer - Cabin Materials", "", "DE", cfg).role_fit == 0.0
    assert evaluate("Research Scientist, Metallurgy", "", "DE", cfg).role_fit == 0.0


def test_research_modifier_never_pushes_role_fit_above_one(cfg):
    cfg.role_modifiers = RoleModifiers(weight=0.9, keywords=["research"])
    r = evaluate("GNSS Galileo Receiver Research Engineer",
                 "gnss galileo receiver research", "DE", cfg)
    assert r.role_fit == 1.0


def test_no_modifier_configured_leaves_scores_untouched(cfg):
    assert evaluate("GNSS Research Engineer", "", "DE", cfg).modifiers == []


def test_r_and_t_is_matched_as_a_whole_token(cfg):
    """'r&t' is short enough for boundary matching; it must not hit 'part'."""
    cfg.role_modifiers = RoleModifiers(weight=0.15, keywords=["r&t"])
    assert evaluate("Ingénieur R&T RF MEMS GNSS", "", "FR", cfg).modifiers == ["r&t"]
    assert evaluate("GNSS Parts Engineer", "", "FR", cfg).modifiers == []


# --- ambiguous keywords, found by polling consulting boards -------------

def _live_cfg():
    from pathlib import Path

    from jobhunt.config import load_scoring

    return load_scoring(Path("config/scoring.yaml"))


def test_ppp_the_financing_model_is_not_precise_point_positioning():
    """Consulting boards are full of infrastructure work. 'PPP Expert /
    Financial Analyst' outscored a real Toulouse GNSS architect role, because
    bare 'ppp' means public-private partnership far more often than it means
    precise point positioning."""
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    finance = evaluate("PPP Expert / Financial Analyst", "", "IN", cfg)
    gnss = evaluate("Architecte système en navigation par satellites", "", "FR", cfg)
    assert finance.role_fit < gnss.role_fit


def test_cost_estimation_is_not_state_estimation():
    from jobhunt.match import evaluate

    assert not evaluate("Design & Estimation Expert", "", "IN", _live_cfg()).relevant


def test_the_real_estimation_terms_still_match():
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    for title in ("State Estimation Engineer", "Orbit Determination Analyst",
                  "Kalman Filter Engineer", "Precise Point Positioning Specialist"):
        assert evaluate(title, "", "FR", cfg).relevant, title


def test_the_search_stays_in_europe():
    """SmartRecruiters reports a real per-posting country, so out-of-scope
    postings can finally be dropped instead of stored as French ones."""
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    assert not evaluate("GNSS Systems Specialist", "", "CA", cfg).relevant
    assert not evaluate("SAR Payload System Engineer", "", "IN", cfg).relevant
    assert evaluate("GNSS Systems Specialist", "", "FR", cfg).relevant


# --- French navigation vocabulary, found on ONERA's doctoral pages ------

def test_french_navigation_terms_match():
    """ONERA writes its theses in French. Three real doctoral offers scored
    zero: space-debris localisation, VTOL guidance, and swarm localisation."""
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    for title in (
        "Localisation de débris spatiaux dans les basses orbites de la Terre",
        "Stabilisation et guidage de micro-drones hybrides VTOL",
        "Coordination d'un essaim de drones pour la recherche et localisation",
        "Trajectographie passive par filtrage particulaire",
        "Recalage de navigation inertielle par vision",
    ):
        assert evaluate(title, "", "FR", cfg).relevant, title


def test_statistical_estimation_is_still_not_navigation():
    """The same pages carry extreme-value-theory theses. 'Estimation' there
    means statistics, and they must stay out."""
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    for title in (
        "IA générative et théorie des valeurs extrêmes : estimation des queues",
        "Prédiction conforme pour l'estimation d'évènements rares",
    ):
        assert not evaluate(title, "", "FR", cfg).relevant, title


def test_project_management_french_is_not_guidance():
    """'Pilotage' alone is business French for steering a project, so it is
    deliberately not a keyword; only guidage-pilotage is."""
    from jobhunt.match import evaluate

    assert not evaluate("Chargé de pilotage de projet industriel", "", "FR",
                        _live_cfg()).relevant
