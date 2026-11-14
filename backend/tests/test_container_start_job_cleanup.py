"""container_start.sh must kill only job_registry rows that started BEFORE this container (2026-11-14: it killed a
night-pipeline job started 3 s earlier -> false 'Killed: container restarted' critical alert)."""
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent / "scripts" / "container_start.sh").read_text()


def test_stale_job_kill_is_bounded_by_container_start():
    assert "WHERE status='running' AND started_at < $1" in SCRIPT
    assert "WHERE status='running'\n" not in SCRIPT, "the unconditional kill must not come back"


def test_container_start_instant_is_taken_before_the_api_wait():
    assert SCRIPT.index("CONTAINER_START_UTC=") < SCRIPT.index("Waiting for API to be healthy")
