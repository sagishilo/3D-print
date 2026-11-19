"""Backup status for the dashboard (gateway/app/system.py: status.json from infra/backup/backup.sh)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

import importlib  # noqa: E402

from gateway_support import gateway_client, login  # noqa: E402

system = importlib.import_module("app.system")   # importable once gateway_support has put gateway/ on the path

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def status(**over) -> dict:
    return {"interval_s": 86400, "offsite_enabled": True, "local_ok_at": iso(3), "local_error": "", "local_error_at": "",
            "offsite_ok_at": iso(3), "offsite_error": "", "offsite_error_at": "", **over}


def health(**over) -> dict:
    return system.backup_health(status(**over), NOW.timestamp())


def test_recent_local_and_offsite_is_ok():
    h = health()
    assert h["state"] == "ok" and h["problems"] == [] and round(h["local_age_s"]) == 3 * 3600


def test_local_only_is_its_own_state():
    assert health(offsite_enabled=False, offsite_ok_at="").get("state") == "local_only"


def test_stale_after_two_intervals():
    h = health(local_ok_at=iso(50), offsite_ok_at=iso(49))
    assert h["state"] == "problem"
    assert any("local backup is 50 h old" in p for p in h["problems"])
    assert any("off-site copy is 49 h old" in p for p in h["problems"])


def test_a_failure_after_the_last_success_is_reported():
    h = health(offsite_error="upload failed: 401", offsite_error_at=iso(1))
    assert h["state"] == "problem" and "upload failed: 401" in h["problems"][0]
    # an old failure, followed by a success, is not
    assert health(offsite_error="upload failed", offsite_error_at=iso(10))["state"] == "ok"


def test_remote_without_a_key_is_reported_even_when_offsite_is_off():
    h = health(offsite_enabled=False, offsite_ok_at="", offsite_error="BACKUP_REMOTE is set but BACKUP_AGE_RECIPIENT is not")
    assert h["state"] == "problem" and "BACKUP_AGE_RECIPIENT" in h["problems"][0]


def test_no_status_file_yet():
    assert system.backup_health(None, NOW.timestamp())["state"] == "unknown"


def test_endpoint_reads_the_status_file(tmp_path, monkeypatch):
    f = tmp_path / "status.json"
    f.write_text(json.dumps(status(local_ok_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   offsite_ok_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))), encoding="utf-8")
    monkeypatch.setattr(system.settings, "backup_status_file", str(f))
    with gateway_client() as c:
        assert c.get("/api/v1/system/backups").status_code == 401
        login(c)
        assert c.get("/api/v1/system/backups").json()["state"] == "ok"
        monkeypatch.setattr(system.settings, "backup_status_file", str(tmp_path / "missing.json"))
        assert c.get("/api/v1/system/backups").json()["state"] == "unknown"
