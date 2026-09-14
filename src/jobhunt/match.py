from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from jobhunt.config import RoleFamily, ScoringConfig

TITLE_WEIGHT = 2.0
DESCRIPTION_WEIGHT = 1.0
# Hits needed in the title alone for a family to reach its full weight.
SATURATION = 2.0

# The one rejection an advert can overturn: the title simply said too little.
# Callers that go and fetch a posting's own page key off this.
NO_FAMILY_MATCHED = "no role family matched"


@dataclass
class MatchResult:
    relevant: bool
    role_fit: float
    matched_families: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    language_flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


# Keywords this short are domain abbreviations, and matching them as
# substrings produces nonsense: 'ins' (inertial navigation) hits Installer,
# Inspecteur and Insurance; 'sar' hits Sarah. Anything longer is distinctive
# enough that plain substring matching is safe — and substring matching is
# what makes 'navigation system' match 'Navigation Systems Engineer', which
# strict boundaries silently dropped.
# Both halves of this rule come from live poll results, not theory.
BOUNDARY_MAX_LENGTH = 4


def fold(text: str) -> str:
    """Lowercase and strip accents, so one spelling of a keyword matches them all.

    French and German postings are not written to a house style: the same
    posting board carries "fusion de données" and "fusion de donnees",
    "Empfänger" and "EMPFAENGER" is rarer but "Störunterdrückung" appears
    unaccented in plain-text exports. Matching the literal string meant a
    keyword list had to carry every variant — config/scoring.yaml already
    listed both "études amont" and "etudes amont" for exactly this reason.

    ß is folded to ss because it decomposes to nothing under NFKD.
    """
    lowered = (text or "").lower().replace("ß", "ss")
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@lru_cache(maxsize=2048)
def _folded_keyword(keyword: str) -> str:
    """Keywords come from config and repeat on every posting; haystacks do not,
    so only this side is cached."""
    return fold(keyword)


@lru_cache(maxsize=1024)
def _keyword_pattern(keyword: str) -> re.Pattern[str] | None:
    if len(keyword) > BOUNDARY_MAX_LENGTH:
        return None
    escaped = re.escape(keyword)
    left = r"\b" if keyword[:1].isalnum() else ""
    right = r"\b" if keyword[-1:].isalnum() else ""
    return re.compile(f"{left}{escaped}{right}")


@lru_cache(maxsize=1024)
def _word_start_pattern(keyword: str) -> re.Pattern[str]:
    """A keyword that must begin a word, but may end mid-one.

    The trailing half is left open on purpose: `navigation system` still has to
    match "Navigation Systems Engineer", and German compounds still have to
    match `empfanger` inside "Satellitenempfänger".
    """
    left = r"\b" if keyword[:1].isalnum() else ""
    return re.compile(f"{left}{re.escape(keyword)}")


def _matches(keyword: str, haystack: str, word_start: bool = False) -> bool:
    """The haystack is expected folded already; evaluate() does that once.

    `word_start` is for the rejection lists. Matching a bare substring there
    means `formation` rejects "Geo Information" and `communication` rejects
    "Communication, Navigation" — a real posting silently thrown away. The
    role families keep plain substring matching, because the two mistakes are
    not the same size: an over-eager positive inflates a score you can see, an
    over-eager negative deletes a job you never learn existed.
    """
    keyword = _folded_keyword(keyword)
    if word_start:
        return bool(_word_start_pattern(keyword).search(haystack))
    pattern = _keyword_pattern(keyword)
    if pattern is None:
        return keyword in haystack
    return bool(pattern.search(haystack))


def _family_score(family: RoleFamily, title: str, description: str) -> float:
    hits = 0.0
    for keyword in family.keywords:
        if _matches(keyword, title):
            hits += TITLE_WEIGHT
        elif _matches(keyword, description):
            hits += DESCRIPTION_WEIGHT
    if hits == 0:
        return 0.0
    saturated = min(hits / (TITLE_WEIGHT * SATURATION), 1.0)
    return saturated * family.weight


# Distinct keywords a posting must hit when the only evidence is in the body.
CORROBORATION = 2


def _has_enough_evidence(cfg: ScoringConfig, title_l: str, desc_l: str) -> bool:
    """Whether the keywords found are worth believing.

    A title is written about the job, so one keyword there stands for itself.
    A two-thousand-word advert is not: it describes the division, the site,
    the tooling and the legal entity, and somewhere in all that one domain
    word turns up by accident. Reading full adverts made that the dominant
    false positive — "GPS" in a mechanical design advert is Geometrical
    Product Specification, "receiving" is a loading bay, and a training
    administrator supports users with "navigation queries".

    So body-only evidence has to corroborate itself: two distinct keywords,
    counted across all families because "gnss" plus "navigation" is exactly
    the pair that should pass.
    """
    keywords = [k for family in cfg.role_families for k in family.keywords]
    if any(_matches(k, title_l) for k in keywords):
        return True
    return len({k for k in keywords if _matches(k, desc_l)}) >= CORROBORATION


def evaluate(
    title: str, description: str, country: str, cfg: ScoringConfig,
    level: str = "",
) -> MatchResult:
    """Score a posting for relevance. Never mutates cfg; safe to call repeatedly.

    `level` exists for one distinction: the country exclusion was written for
    the right to work, and a funded doctorate is not employment. A posting at
    level "phd" in a country named in `phd_countries` is kept. Callers that
    know nothing about level get the strict rule.
    """
    title_l = fold(title)
    desc_l = fold(description)
    reasons: list[str] = []

    if country and country.upper() in {c.upper() for c in cfg.excluded_countries}:
        doctoral_exception = (
            level == "phd"
            and country.upper() in {c.upper() for c in cfg.phd_countries}
        )
        if not doctoral_exception:
            return MatchResult(
                relevant=False, role_fit=0.0,
                reasons=[f"excluded country: {country.upper()}"],
            )

    for negative in cfg.negative_keywords:
        if _matches(negative, title_l, word_start=True):
            return MatchResult(
                relevant=False, role_fit=0.0,
                reasons=[f"negative keyword in title: {negative}"],
            )

    if not _has_enough_evidence(cfg, title_l, desc_l):
        return MatchResult(relevant=False, role_fit=0.0,
                           reasons=[NO_FAMILY_MATCHED])

    scores = {f.name: _family_score(f, title_l, desc_l) for f in cfg.role_families}
    matched = sorted(
        (name for name, s in scores.items() if s > 0),
        key=lambda name: scores[name], reverse=True,
    )
    role_fit = max(scores.values()) if scores else 0.0

    # Modifiers only ever adjust an existing match. Letting them score alone
    # would rank an R&D role in any discipline above real GNSS work — a live
    # search of one tenant for 'r&d' returned 182 jobs led by QA Engineer.
    modifiers = [
        keyword for keyword in cfg.role_modifiers.keywords
        if _matches(keyword, title_l) or _matches(keyword, desc_l)
    ]
    if role_fit > 0 and modifiers:
        role_fit += cfg.role_modifiers.weight

    role_fit = min(max(role_fit, 0.0), 1.0)

    language_flags = []
    known = {lang.lower() for lang in cfg.known_languages}
    for lang, keywords in cfg.language_keywords.items():
        if lang.lower() in known:
            continue
        if any(_matches(k, title_l) or _matches(k, desc_l) for k in keywords):
            language_flags.append(lang)

    if role_fit == 0.0:
        reasons.append(NO_FAMILY_MATCHED)

    return MatchResult(
        relevant=role_fit > 0.0,
        role_fit=role_fit,
        matched_families=matched,
        modifiers=modifiers,
        language_flags=sorted(language_flags),
        reasons=reasons,
    )
