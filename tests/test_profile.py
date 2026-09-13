import pytest

from jobhunt.cli import _profile, _substantive


def test_substantive_drops_headings_and_empty_bullets():
    text = _substantive("# Evidence\n\n## Telespazio internship\n\n- \n- \n")
    assert text == ""


def test_substantive_keeps_real_content():
    text = _substantive("# Evidence\n\n- Built a Galileo E1 acquisition engine\n")
    assert "Galileo E1" in text


def test_profile_falls_back_when_the_template_is_unfilled(tmp_path, monkeypatch):
    """A template full of headings reads as non-empty but says nothing.

    Live check: passed through unchanged, the model refused to judge and
    returned 0.00 for a posting the rule-based matcher scored 1.00.
    """
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / "positioning.md").write_text("# Positioning\n\n## Angle\n\n- \n")
    (profile_dir / "evidence.md").write_text("# Evidence\n\n## Internship\n")
    monkeypatch.setenv("JOBHUNT_PROFILE", str(profile_dir))
    monkeypatch.setenv("JOBHUNT_CONFIG", "config")

    profile = _profile()
    assert "Background notes" not in profile
    assert "gnss" in profile.lower()


def test_profile_uses_the_files_once_they_are_filled_in(tmp_path, monkeypatch):
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / "evidence.md").write_text(
        "# Evidence\n\n- Built a Galileo E1 acquisition and tracking engine in C++\n"
        "- Six-month Telespazio internship on GNSS receiver validation\n"
        "- MSc thesis on multipath mitigation for urban positioning\n"
    )
    monkeypatch.setenv("JOBHUNT_PROFILE", str(profile_dir))
    profile = _profile()
    assert "Galileo E1" in profile
    assert "Target domains" in profile  # the derived facts stay regardless


def test_substantive_drops_a_prompt_with_no_answer():
    """The template's bullets are labels waiting for content — "- Methods:" —
    and they are far longer than three characters, so the emptiness test never
    saw them. Both shipped files cleared the threshold on their headings alone
    and a page of unanswered prompts went to the model as the candidate's
    background."""
    text = _substantive("- Role, dates, team:\n- Problem worked on:\n"
                        "- Methods and tools used:\n")
    assert text == ""


def test_substantive_keeps_a_prompt_that_was_answered():
    text = _substantive("- Methods and tools used: GNSS-SDR, gnuradio, C++\n")
    assert "GNSS-SDR" in text


def test_substantive_drops_the_templates_own_guidance():
    """Italic and plain instruction lines describe how to fill the file in.
    They are prose, so nothing above catches them, and they read to the model
    as claims about the candidate."""
    text = _substantive(
        "Two to four angles you can credibly lead with. Same person, "
        "different emphasis.\n")
    assert text == ""


def test_the_shipped_template_is_not_treated_as_a_profile(tmp_path, monkeypatch):
    """The guard exists so an unfilled profile falls back to what
    scoring.yaml states, rather than dressing up empty headings as evidence."""
    from pathlib import Path

    source = Path("profile")
    if not source.exists():                     # pragma: no cover
        import pytest
        pytest.skip("no profile/ directory in this checkout")
    target = tmp_path / "profile"
    target.mkdir()
    for name in ("positioning.md", "evidence.md"):
        if (source / name).exists():
            (target / name).write_text((source / name).read_text(
                encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("JOBHUNT_PROFILE", str(target))
    assert "Background notes:" not in _profile()
