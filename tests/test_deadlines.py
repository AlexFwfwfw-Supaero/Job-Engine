from datetime import date

from jobhunt.deadlines import upcoming
from jobhunt.models import Deadline


def test_a_deadline_inside_its_lead_window_is_returned():
    d = Deadline(name="YGT", closes="2026-09-01", lead_days=60)
    result = upcoming([d], date(2026, 7, 26))
    assert len(result) == 1
    assert result[0].days_left == 37


def test_a_deadline_beyond_its_lead_window_is_hidden():
    d = Deadline(name="Far", closes="2027-06-01", lead_days=30)
    assert upcoming([d], date(2026, 7, 26)) == []


def test_a_closed_deadline_is_hidden():
    d = Deadline(name="Gone", closes="2026-07-01", lead_days=60)
    assert upcoming([d], date(2026, 7, 26)) == []


def test_not_yet_open_is_flagged_opening_soon():
    d = Deadline(name="YGT", opens="2026-09-01", closes="2026-11-15", lead_days=120)
    assert upcoming([d], date(2026, 7, 26))[0].state == "opening_soon"


def test_currently_open_is_flagged_open():
    d = Deadline(name="YGT", opens="2026-07-01", closes="2026-09-15", lead_days=120)
    assert upcoming([d], date(2026, 7, 26))[0].state == "open"


def test_open_and_closing_within_two_weeks_is_closing_soon():
    d = Deadline(name="Intake", opens="2026-06-01", closes="2026-08-05", lead_days=120)
    assert upcoming([d], date(2026, 7, 26))[0].state == "closing_soon"


def test_deadlines_without_a_close_date_are_hidden():
    assert upcoming([Deadline(name="Rolling")], date(2026, 7, 26)) == []


def test_results_are_sorted_by_urgency():
    a = Deadline(name="A", closes="2026-09-01", lead_days=90)
    b = Deadline(name="B", closes="2026-08-10", lead_days=90)
    assert [u.name for u in upcoming([a, b], date(2026, 7, 26))] == ["B", "A"]
