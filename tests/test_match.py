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


# --- French and German postings ----------------------------------------

def test_accents_are_folded_so_one_spelling_covers_both():
    """Boards are inconsistent: the same term appears accented or not."""
    from jobhunt.match import evaluate, fold

    assert fold("Fusion de Données") == "fusion de donnees"
    assert fold("GNSS-Empfänger") == "gnss-empfanger"
    assert fold("Störunterdrückung") == "storunterdruckung"

    cfg = _live_cfg()
    accented = evaluate("Ingénieur", "Fusion de données pour la navigation inertielle", "FR", cfg)
    plain = evaluate("Ingenieur", "Fusion de donnees pour la navigation inertielle", "FR", cfg)
    assert accented.role_fit == plain.role_fit > 0


def test_a_german_posting_scores_like_its_english_twin():
    """Six polled employers post in German and had no keyword of their own,
    so an identical job scored half of what the English version did."""
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    english = evaluate(
        "GNSS Receiver Signal Processing Engineer",
        "Work on GNSS receiver acquisition and tracking, anti-jamming.",
        "DE", cfg)
    german = evaluate(
        "Ingenieur Signalverarbeitung GNSS-Empfänger",
        "Entwicklung eines GNSS-Empfängers, Störunterdrückung.",
        "DE", cfg)
    assert german.role_fit == english.role_fit == 1.0


def test_a_french_posting_scores_like_its_english_twin():
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    english = evaluate(
        "GNSS Receiver Signal Processing Engineer",
        "Work on GNSS receiver acquisition and tracking, anti-jamming.",
        "FR", cfg)
    french = evaluate(
        "Ingénieur traitement du signal récepteur GNSS",
        "Conception d'un récepteur GNSS, acquisition et poursuite, "
        "anti-brouillage.",
        "FR", cfg)
    assert french.role_fit == english.role_fit == 1.0


def test_sales_roles_are_excluded_in_french_and_german_too():
    """The negative list was English-only, so both of these were stored."""
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    assert not evaluate("Vertriebsingenieur Navigation", "", "DE", cfg).relevant
    assert not evaluate("Ingénieur commercial navigation", "", "FR",
                        cfg).relevant
    assert not evaluate("Chargé d'affaires radar", "", "FR", cfg).relevant


def test_german_engineering_roles_are_still_kept():
    """The German negative keywords must not swallow real work."""
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    for title in (
        "Ingenieur Trägheitsnavigation und Sensorfusion",
        "Entwicklungsingenieur Radartechnik",
        "Doktorand Signalverarbeitung für Satellitennavigation",
    ):
        assert evaluate(title, "", "DE", cfg).relevant, title


def test_a_negative_keyword_must_start_a_word():
    """Plain substring matching made 'formation' reject 'Geo Information' and
    'communication' reject 'Communication, Navigation' — real radar and remote
    sensing work thrown away with nothing said."""
    from jobhunt.config import ScoringConfig
    from jobhunt.match import evaluate

    cfg = ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[RoleFamily("radar", 1.0, ["radar", "remote sensing"])],
        negative_keywords=["formation", "communication"],
    )
    assert evaluate("Internship Radar Remote Sensing and Geo Information",
                    "", "DE", cfg).relevant
    assert not evaluate("Formation Engineer Radar", "", "DE", cfg).relevant


def test_a_positive_keyword_still_matches_inside_a_word():
    """The asymmetry is the point: 'navigation system' has to reach
    'Navigation Systems', and German compounds swallow their keywords."""
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    assert evaluate("Navigation Systems Engineer", "", "DE", cfg).relevant
    assert evaluate("Entwicklungsingenieur Radartechnik", "", "DE", cfg).relevant


def test_technician_and_admin_roles_are_rejected_outright():
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    for title in (
        "Technicien d'essais en électronique numérique & RF (H/F)",
        "Avionics Technician (m/f/d)",
        "HF-Techniker:in für Antennenbetrieb (m/w/d)",
        "Data Delivery Operator (Navigation+)",
        "Assistant Technique Client Radar SAMP/T (H/F)",
    ):
        assert not evaluate(title, "", "FR", cfg).relevant, title


def test_technical_leads_and_quality_engineers_survive():
    """Rejecting 'responsable' or 'quality' by title would have deleted jobs
    already shortlisted; the screening prompt scores them down instead."""
    from jobhunt.match import evaluate

    cfg = _live_cfg()
    for title in (
        "Ingénieur Responsable technique de station de réception GNSS",
        "Avionics Quality Engineer (m/f/d)",
        "GALILEO Engineering Work Package Manager",
    ):
        assert evaluate(title, "", "FR", cfg).relevant, title


def test_bare_integrity_is_not_a_pnt_keyword():
    """Structural integrity, data integrity, personal integrity — the word is
    everywhere in engineering adverts. In PNT it only means something when it
    is qualified, so the qualified forms are what the family carries. Safran's
    board matched a Dynamic FEA Engineer on "structural integrity" alone."""
    cfg = _live_cfg()
    body = ("This position ensures the structural integrity, reliability and "
            "performance optimization of our mechanical products.")
    assert evaluate("Dynamic FEA Engineer", body, "FR", cfg).role_fit == 0.0


def test_qualified_integrity_still_matches():
    cfg = _live_cfg()
    body = "Design of integrity monitoring for a civil aviation GNSS receiver."
    assert evaluate("PNT Engineer", body, "FR", cfg).role_fit > 0.0


def test_one_keyword_deep_in_an_advert_is_not_enough_on_its_own():
    """Reading full adverts made this the dominant kind of false positive.
    A mechanical design advert says "GPS" meaning Geometrical Product
    Specification; a shipping role says "receiving"; a training administrator
    says users have "navigation queries" in the LMS. Each is one word in two
    thousand, and each was scoring as navigation work."""
    cfg = _live_cfg()
    body = ("Conception mécanique de pièces moteur, cotation fonctionnelle et "
            "spécification géométrique GPS selon ISO 1101. Calculs de "
            "dimensionnement et revues de définition.")
    assert evaluate("Ingénieur conception mécanique", body, "FR", cfg).role_fit == 0.0


def test_two_keywords_in_an_advert_are_enough():
    """Real work says it more than once, and in more than one way."""
    cfg = _live_cfg()
    body = ("Vous rejoignez l'équipe navigation inertielle et contribuez au "
            "traitement du signal des récepteurs GNSS embarqués.")
    assert evaluate("Ingénieur études F/H", body, "FR", cfg).role_fit > 0.0


def test_the_title_still_speaks_for_itself():
    """A title is written about the job, so one keyword there stands alone —
    unlike one buried in a page of corporate description."""
    cfg = _live_cfg()
    assert evaluate("Ingénieur GNSS F/H", "", "FR", cfg).role_fit > 0.0


def test_drone_guidance_and_control_work_is_matched():
    """ONERA's doctoral pages are the evidence. "Apprentissage par
    renforcement profond pour la commande de drones" and "Coordination d'un
    essaim de drones" matched no keyword at all: the families had "gnc" and
    "flight dynamics" but neither "drone" nor the French word for control.
    Guidance and control of an air vehicle is the C in GNC."""
    cfg = _live_cfg()
    for title in [
        "Apprentissage par renforcement profond pour la commande de drones",
        "Deep reinforcement learning for UAV control",
        "Commande et automatique des véhicules autonomes",
    ]:
        assert evaluate(title, "", "FR", cfg).role_fit > 0, title


def test_control_of_a_flow_or_a_process_is_not_vehicle_control():
    """The counterweight. ONERA also runs "Modélisation et contrôle en boucle
    fermée d'écoulements" and thermoacoustic engine control, which are
    fluid-dynamics theses. Bare "contrôle" would take both, so the keywords
    stay qualified."""
    cfg = _live_cfg()
    for title in [
        "Modélisation et contrôle en boucle fermée d'écoulements résonateurs",
        "Analysis of thermoacoustic phase-change driven engine: stability and control",
        "Contrôle qualité des pièces usinées",
    ]:
        assert evaluate(title, "", "FR", cfg).role_fit == 0.0, title
