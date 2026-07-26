from jobhunt.models import Stage, EventKind, is_terminal, Job


def test_terminal_stages_are_terminal():
    assert is_terminal(Stage.REJECTED)
    assert is_terminal(Stage.WITHDRAWN)
    assert is_terminal(Stage.EXPIRED)


def test_active_stages_are_not_terminal():
    assert not is_terminal(Stage.SPOTTED)
    assert not is_terminal(Stage.APPLIED)
    assert not is_terminal(Stage.OFFER)


def test_stage_serialises_to_its_string_value():
    assert Stage.APPLIED.value == "applied"
    assert Stage("applied") is Stage.APPLIED


def test_event_kinds_exist():
    assert EventKind.STAGE.value == "stage"
    assert EventKind.NOTE.value == "note"
    assert EventKind.FOLLOWUP.value == "followup"
    assert EventKind.DISMISS.value == "dismiss"


def test_job_defaults_to_spotted_and_not_dismissed():
    job = Job(employer_id=1, title="GNSS Engineer", url="https://example.com/1")
    assert job.stage is Stage.SPOTTED
    assert job.dismissed is False
    assert job.tags == []
