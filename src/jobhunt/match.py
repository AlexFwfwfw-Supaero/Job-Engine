from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from jobhunt.config import RoleFamily, ScoringConfig

TITLE_WEIGHT = 2.0
DESCRIPTION_WEIGHT = 1.0
# Hits needed in the title alone for a family to reach its full weight.
SATURATION = 2.0


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


@lru_cache(maxsize=1024)
def _keyword_pattern(keyword: str) -> re.Pattern[str] | None:
    if len(keyword) > BOUNDARY_MAX_LENGTH:
        return None
    escaped = re.escape(keyword)
    left = r"\b" if keyword[:1].isalnum() else ""
    right = r"\b" if keyword[-1:].isalnum() else ""
    return re.compile(f"{left}{escaped}{right}")


def _matches(keyword: str, haystack: str) -> bool:
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


def evaluate(
    title: str, description: str, country: str, cfg: ScoringConfig
) -> MatchResult:
    """Score a posting for relevance. Never mutates cfg; safe to call repeatedly."""
    title_l = (title or "").lower()
    desc_l = (description or "").lower()
    reasons: list[str] = []

    if country and country.upper() in {c.upper() for c in cfg.excluded_countries}:
        return MatchResult(
            relevant=False, role_fit=0.0,
            reasons=[f"excluded country: {country.upper()}"],
        )

    for negative in cfg.negative_keywords:
        if _matches(negative, title_l):
            return MatchResult(
                relevant=False, role_fit=0.0,
                reasons=[f"negative keyword in title: {negative}"],
            )

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
        reasons.append("no role family matched")

    return MatchResult(
        relevant=role_fit > 0.0,
        role_fit=role_fit,
        matched_families=matched,
        modifiers=modifiers,
        language_flags=sorted(language_flags),
        reasons=reasons,
    )
