"""``dbreactivation`` command line interface.

Subcommands::

    rank      score + order dormant leads with the factor breakdown
    schedule  build a compliant call schedule (readiness-gated)
    ingest    apply call outcomes back into lead state + suppression
    status    campaign snapshot: leads per state, attempts, suppression
    check     run the readiness gate on its own

Every subcommand accepts ``--json`` for machine-readable output. Exit codes:
0 success, 1 usage/data error, 3 readiness gate refused.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .intake import OutcomeRecord, ingest_outcomes
from .models import (
    CampaignConfig,
    Lead,
    LeadState,
    SuppressionList,
    ensure_utc,
    parse_when,
)
from .ranking import rank_leads
from .readiness import run_readiness
from .scheduler import build_schedule

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_READY = 3


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_leads(path: str) -> List[Lead]:
    return [Lead.from_dict(item) for item in _read_json(path)]


def _load_config(path: Optional[str]) -> CampaignConfig:
    if not path:
        return CampaignConfig()
    return CampaignConfig.from_dict(_read_json(path))


def _load_suppression(path: Optional[str]) -> SuppressionList:
    if not path or not Path(path).exists():
        return SuppressionList()
    return SuppressionList.from_text(Path(path).read_text(encoding="utf-8"))


def _now(args: argparse.Namespace) -> datetime:
    if getattr(args, "now", None):
        parsed = parse_when(args.now)
        if parsed is not None:
            return parsed
    return ensure_utc(datetime.now(timezone.utc))


def _emit(payload: Dict[str, Any], as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(human)


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------


def cmd_rank(args: argparse.Namespace) -> int:
    leads = _load_leads(args.leads)
    ranked = rank_leads(leads, now=_now(args))
    if args.top:
        ranked = ranked[: args.top]

    if args.json:
        print(json.dumps({"ranked": [r.to_dict() for r in ranked]}, indent=2))
        return EXIT_OK

    print(f"{'#':>3}  {'LEAD':<10} {'NAME':<22} {'SCORE':>6}  FACTORS")
    print("-" * 78)
    for i, r in enumerate(ranked, 1):
        factors = "  ".join(f"{f.name}={f.value:.2f}" for f in r.factors)
        print(f"{i:>3}  {r.lead.lead_id:<10} {r.lead.name[:22]:<22} {r.score:>6.1f}  {factors}")
    print("-" * 78)
    print(f"{len(ranked)} lead(s) ranked")
    return EXIT_OK


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------


def cmd_schedule(args: argparse.Namespace) -> int:
    if not args.skip_readiness:
        report = run_readiness(
            Path(args.config) if args.config else Path("missing-config.json"),
            Path(args.leads),
            Path(args.suppression) if args.suppression else None,
        )
        if not report.ready:
            failing = [c for c in report.checks if c.required and not c.ok]
            if args.json:
                print(json.dumps({"error": "not_ready", **report.to_dict()}, indent=2))
            else:
                print("REFUSED - readiness gate failed:")
                for c in failing:
                    print(f"  [FAIL] {c.name}: {c.status} - {c.detail}")
                print("Fix the above or rerun with --skip-readiness (not recommended).")
            return EXIT_NOT_READY

    leads = _load_leads(args.leads)
    config = _load_config(args.config)
    suppression = _load_suppression(args.suppression)
    schedule = build_schedule(leads, config, suppression=suppression, now=_now(args))

    if args.json:
        print(json.dumps(schedule.to_dict(), indent=2))
        return EXIT_OK

    print(f"{'WHEN (UTC)':<18} {'LEAD LOCAL':<38} {'LEAD':<10} {'ATT':>3}  SCORE")
    print("-" * 80)
    for call in schedule.calls:
        when = call.scheduled_at.strftime("%Y-%m-%d %H:%M")
        print(
            f"{when:<18} {call.lead_local:<38} {call.lead_id:<10} "
            f"{call.attempt_number:>3}  {call.score:.1f}"
        )
    print("-" * 80)
    print(f"{len(schedule.calls)} call(s) scheduled | {len(schedule.skipped)} skipped")
    for s in schedule.skipped:
        print(f"  skipped {s.lead_id}: {s.reason}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def cmd_ingest(args: argparse.Namespace) -> int:
    leads = _load_leads(args.leads)
    config = _load_config(args.config)
    suppression = _load_suppression(args.suppression)
    records = [OutcomeRecord.from_dict(item) for item in _read_json(args.outcomes)]

    report = ingest_outcomes(leads, records, config, suppression)

    if args.out:
        Path(args.out).write_text(
            json.dumps([lead.to_dict() for lead in leads], indent=2) + "\n",
            encoding="utf-8",
        )
    if args.suppression and report.suppression_additions:
        Path(args.suppression).write_text(suppression.to_text(), encoding="utf-8")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return EXIT_OK if not report.errors else EXIT_ERROR

    print(f"{'LEAD':<10} {'OUTCOME':<10} {'NEW STATE':<12} {'ATT':>3}  NOTES")
    print("-" * 64)
    for row in report.applied:
        notes = []
        if row["opted_out"]:
            notes.append("OPT-OUT -> suppressed")
        if row["booked"]:
            notes.append("BOOKED")
        elif row["interested"]:
            notes.append("interested")
        if row["cooldown_until"]:
            notes.append(f"cooldown until {row['cooldown_until']}")
        print(
            f"{row['lead_id']:<10} {row['outcome']:<10} {row['state']:<12} "
            f"{row['attempts']:>3}  {'; '.join(notes)}"
        )
    print("-" * 64)
    print(
        f"{len(report.applied)} outcome(s) applied | "
        f"{len(report.suppression_additions)} new suppression(s) | "
        f"{len(report.errors)} error(s)"
    )
    for err in report.errors:
        print(f"  error {err['lead_id']}: {err['error']}")
    return EXIT_OK if not report.errors else EXIT_ERROR


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    leads = _load_leads(args.leads)
    suppression = _load_suppression(args.suppression)

    counts = {state.value: 0 for state in LeadState}
    for lead in leads:
        counts[lead.state.value] += 1
    total_attempts = sum(lead.attempts for lead in leads)
    booked = counts[LeadState.BOOKED.value]
    opted_out = counts[LeadState.OPTED_OUT.value]

    payload = {
        "leads_total": len(leads),
        "states": {k: v for k, v in counts.items() if v},
        "total_attempts": total_attempts,
        "booked": booked,
        "opted_out": opted_out,
        "suppressed_numbers": len(suppression),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return EXIT_OK

    print(f"CAMPAIGN STATUS - {len(leads)} lead(s)")
    print("-" * 40)
    for state in LeadState:
        if counts[state.value]:
            print(f"  {state.value:<12} {counts[state.value]:>4}")
    print("-" * 40)
    print(f"  attempts made      {total_attempts}")
    print(f"  booked             {booked}")
    print(f"  opted out          {opted_out}")
    print(f"  suppression list   {len(suppression)} number(s)")
    return EXIT_OK


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    report = run_readiness(
        Path(args.config) if args.config else Path("missing-config.json"),
        Path(args.leads),
        Path(args.suppression) if args.suppression else None,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        for c in report.checks:
            mark = "PASS" if c.ok else ("FAIL" if c.required else "WARN")
            print(f"  [{mark}] {c.name:<18} {c.status:<22} {c.detail}")
        print("-" * 64)
        print("READY - campaign may start" if report.ready else "NOT READY - refusing to start")
    return EXIT_OK if report.ready else EXIT_NOT_READY


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbreactivation",
        description="Win-back engine for dormant leads: rank, schedule, ingest, status.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, config: bool = True, suppression: bool = True):
        p.add_argument("--leads", required=True, help="path to leads JSON file")
        if config:
            p.add_argument("--config", help="path to campaign config JSON")
        if suppression:
            p.add_argument("--suppression", help="path to suppression list (one number/line)")
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.add_argument("--now", help="override clock (ISO-8601, for deterministic runs)")

    p_rank = sub.add_parser("rank", help="rank dormant leads by reactivation potential")
    common(p_rank, suppression=False)
    p_rank.add_argument("--top", type=int, default=0, help="only show the top N leads")
    p_rank.set_defaults(func=cmd_rank)

    p_sched = sub.add_parser("schedule", help="build a compliant call schedule")
    common(p_sched)
    p_sched.add_argument(
        "--skip-readiness", action="store_true", help="bypass the readiness gate"
    )
    p_sched.set_defaults(func=cmd_schedule)

    p_ingest = sub.add_parser("ingest", help="apply call outcomes to campaign state")
    common(p_ingest)
    p_ingest.add_argument("--outcomes", required=True, help="path to outcomes JSON file")
    p_ingest.add_argument("--out", help="write updated leads JSON here")
    p_ingest.set_defaults(func=cmd_ingest)

    p_status = sub.add_parser("status", help="campaign state snapshot")
    common(p_status, config=False)
    p_status.set_defaults(func=cmd_status)

    p_check = sub.add_parser("check", help="run the readiness gate")
    common(p_check)
    p_check.set_defaults(func=cmd_check)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: file not found: {exc.filename}", file=sys.stderr)
        return EXIT_ERROR
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
