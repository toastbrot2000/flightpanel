"""tools/escrow.py: Abhängigkeiten erst nach der Wartezeit übernehmen."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

_spec = importlib.util.spec_from_file_location("escrow", Path(__file__).resolve().parents[2] / "tools" / "escrow.py")
escrow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(escrow)

OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64


def test_only_releases_older_than_the_waiting_period():
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    cut = escrow.cutoff(7, now)
    assert escrow.adopt(now - timedelta(days=8), cut)
    assert not escrow.adopt(now - timedelta(days=2), cut)              # zu frisch: das alte bleibt


def test_both_workflow_jobs_resolve_against_the_same_cutoff():
    assert escrow.parse_time("2026-10-04T20:00:00Z") == datetime(2026, 10, 4, 20, tzinfo=UTC)
    assert escrow.stamp(datetime(2026, 10, 4, 20, tzinfo=UTC)) == "2026-10-04T20:00:00Z"


def test_base_image_digest_is_replaced_everywhere():
    text = f"FROM python:3.12-slim@{OLD}\nRUN true\n  python:3.12-slim@{OLD} sh -c x\n"
    assert escrow.pinned(text, "python:3.12-slim") == OLD
    out = escrow.repin(text, "python:3.12-slim", NEW)
    assert OLD not in out and out.count(f"python:3.12-slim@{NEW}") == 2


def test_lock_changes_are_listed_for_the_commit_message():
    before = "aiohttp==3.14.3 \\\n    --hash=sha256:x\npillow==12.2.0 \\\n    --hash=sha256:y\n"
    after = "aiohttp==3.14.3 \\\n    --hash=sha256:x\npillow==12.3.0 \\\n    --hash=sha256:z\nsgp4==2.25\n"
    assert escrow.diff(escrow.versions(before), escrow.versions(after)) == ["pillow 12.2.0 -> 12.3.0",
                                                                           "sgp4 - -> 2.25"]
