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
