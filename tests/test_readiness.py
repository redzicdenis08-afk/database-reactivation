"""Readiness gate: no campaign starts unless every required check passes."""
from __future__ import annotations

import json
from pathlib import Path

from dbreactivation import CampaignConfig, run_readiness
from dbreactivation.readiness import check_config_values, check_suppression_file

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_example_campaign_is_ready():
    report = run_readiness(
        EXAMPLES / "config.json",
        EXAMPLES / "leads.json",
        EXAMPLES / "suppression.txt",
    )
    assert report.ready, [c.to_dict() for c in report.checks if not c.ok]


def test_missing_suppression_list_refuses_to_start(tmp_path):
    report = run_readiness(EXAMPLES / "config.json", EXAMPLES / "leads.json", None)
    assert not report.ready
    failing = {c.name: c.status for c in report.checks if c.required and not c.ok}
    assert failing == {"suppression_list": "missing"}


def test_empty_suppression_file_is_allowed(tmp_path):
    path = write(tmp_path, "sup.txt", "# fresh campaign, nothing suppressed yet\n")
    check, sup = check_suppression_file(path)
    assert check.ok
    assert len(sup) == 0


def test_malformed_suppression_entries_fail(tmp_path):
    path = write(tmp_path, "sup.txt", "+1-555-0107\nnot-a-number\n")
    check, _ = check_suppression_file(path)
    assert not check.ok
    assert check.status == "malformed_entries"


def test_window_outside_legal_hours_fails():
    checks = check_config_values(CampaignConfig(window_start_hour=7, window_end_hour=18))
    window = next(c for c in checks if c.name == "config_window")
    assert not window.ok
    assert window.status == "outside_legal_window"


def test_inverted_window_fails():
    checks = check_config_values(CampaignConfig(window_start_hour=18, window_end_hour=9))
    window = next(c for c in checks if c.name == "config_window")
    assert window.status == "invalid_window"


def test_bad_limits_fail():
    checks = check_config_values(
        CampaignConfig(daily_cap=0, max_attempts=99, slot_minutes=-5)
    )
    limits = next(c for c in checks if c.name == "config_limits")
    assert not limits.ok
    for fragment in ("daily_cap=0", "max_attempts=99", "slot_minutes=-5"):
        assert fragment in limits.detail


def test_duplicate_lead_ids_fail(tmp_path):
    leads = write(
        tmp_path,
        "leads.json",
        json.dumps(
            [
                {"lead_id": "L-1", "phone": "+1-555-0101"},
                {"lead_id": "L-1", "phone": "+1-555-0102"},
            ]
        ),
    )
    sup = write(tmp_path, "sup.txt", "")
    report = run_readiness(EXAMPLES / "config.json", leads, sup)
    assert not report.ready
    leads_check = next(c for c in report.checks if c.name == "leads")
    assert leads_check.status == "duplicate_ids"


def test_unparseable_leads_file_fails(tmp_path):
    leads = write(tmp_path, "leads.json", "{not json")
    sup = write(tmp_path, "sup.txt", "")
    report = run_readiness(EXAMPLES / "config.json", leads, sup)
    assert not report.ready
    assert any(
        c.name == "leads_file" and c.status == "unreadable" for c in report.checks
    )


def test_report_serializes_for_pipelines():
    report = run_readiness(
        EXAMPLES / "config.json",
        EXAMPLES / "leads.json",
        EXAMPLES / "suppression.txt",
    )
    payload = report.to_dict()
    assert payload["ready"] is True
    assert {"name", "ok", "required", "status", "detail"} <= set(payload["checks"][0])
