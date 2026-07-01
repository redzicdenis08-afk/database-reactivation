"""CLI: the four verbs plus the readiness gate, human and --json output."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from dbreactivation.cli import EXIT_NOT_READY, EXIT_OK, main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = "2026-07-02T14:00:00Z"


def copy_examples(tmp_path: Path) -> dict:
    paths = {}
    for name in ("leads.json", "config.json", "outcomes.json", "suppression.txt"):
        paths[name] = tmp_path / name
        shutil.copy(EXAMPLES / name, paths[name])
    return paths


def run(capsys, *argv) -> tuple:
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_rank_json_is_ordered_and_explained(capsys):
    code, out = run(
        capsys, "rank", "--leads", str(EXAMPLES / "leads.json"), "--json", "--now", NOW
    )
    assert code == EXIT_OK
    ranked = json.loads(out)["ranked"]
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert ranked[0]["lead_id"] == "L-1001"
    assert {f["name"] for f in ranked[0]["factors"]} == {
        "recency",
        "engagement",
        "value",
        "seasonality",
    }


def test_schedule_refuses_without_suppression_list(capsys):
    code, out = run(
        capsys,
        "schedule",
        "--leads", str(EXAMPLES / "leads.json"),
        "--config", str(EXAMPLES / "config.json"),
        "--now", NOW,
    )
    assert code == EXIT_NOT_READY
    assert "REFUSED" in out


def test_schedule_json_end_to_end(capsys):
    code, out = run(
        capsys,
        "schedule",
        "--leads", str(EXAMPLES / "leads.json"),
        "--config", str(EXAMPLES / "config.json"),
        "--suppression", str(EXAMPLES / "suppression.txt"),
        "--json",
        "--now", NOW,
    )
    assert code == EXIT_OK
    payload = json.loads(out)
    scheduled_ids = {c["lead_id"] for c in payload["calls"]}
    skipped = {s["lead_id"]: s["reason"] for s in payload["skipped"]}
    assert len(payload["calls"]) == 6
    assert "L-1007" not in scheduled_ids and skipped["L-1007"] == "suppressed"
    assert skipped["L-1008"] == "state_not_callable:opted_out"


def test_ingest_updates_state_and_suppression(capsys, tmp_path):
    paths = copy_examples(tmp_path)
    out_file = tmp_path / "leads.updated.json"
    code, out = run(
        capsys,
        "ingest",
        "--leads", str(paths["leads.json"]),
        "--config", str(paths["config.json"]),
        "--suppression", str(paths["suppression.txt"]),
        "--outcomes", str(paths["outcomes.json"]),
        "--out", str(out_file),
        "--json",
    )
    assert code == EXIT_OK
    report = json.loads(out)
    by_lead = {row["lead_id"]: row for row in report["applied"]}
    assert by_lead["L-1001"]["state"] == "booked"
    assert by_lead["L-1002"]["state"] == "voicemail"
    assert by_lead["L-1003"]["state"] == "opted_out"
    assert by_lead["L-1005"]["state"] == "no_answer"
    assert report["suppression_additions"] == ["15550103"]

    updated = {
        lead["lead_id"]: lead
        for lead in json.loads(out_file.read_text(encoding="utf-8"))
    }
    assert updated["L-1001"]["state"] == "booked"
    assert updated["L-1002"]["cooldown_until"] == "2026-07-03T15:20:00Z"
    # opt-out is now permanently in the suppression file
    assert "15550103" in paths["suppression.txt"].read_text(encoding="utf-8")


def test_status_summarizes_the_campaign(capsys, tmp_path):
    paths = copy_examples(tmp_path)
    run(
        capsys,
        "ingest",
        "--leads", str(paths["leads.json"]),
        "--config", str(paths["config.json"]),
        "--outcomes", str(paths["outcomes.json"]),
        "--out", str(paths["leads.json"]),
    )
    code, out = run(
        capsys,
        "status",
        "--leads", str(paths["leads.json"]),
        "--suppression", str(paths["suppression.txt"]),
        "--json",
    )
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["leads_total"] == 8
    assert payload["booked"] == 1
    assert payload["states"]["opted_out"] == 2  # L-1008 plus the fresh L-1003
    assert payload["suppressed_numbers"] == 1


def test_check_passes_on_examples_and_fails_without_dnc(capsys):
    code, out = run(
        capsys,
        "check",
        "--leads", str(EXAMPLES / "leads.json"),
        "--config", str(EXAMPLES / "config.json"),
        "--suppression", str(EXAMPLES / "suppression.txt"),
    )
    assert code == EXIT_OK
    assert "READY" in out

    code, out = run(
        capsys,
        "check",
        "--leads", str(EXAMPLES / "leads.json"),
        "--config", str(EXAMPLES / "config.json"),
    )
    assert code == EXIT_NOT_READY
    assert "NOT READY" in out


def test_missing_file_is_a_clean_error(capsys):
    code = main(["rank", "--leads", "does-not-exist.json"])
    assert code == 1
