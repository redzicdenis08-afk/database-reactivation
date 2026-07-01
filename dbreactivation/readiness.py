"""Readiness gate: refuse to start a campaign that isn't safe to run.

A campaign only gets a green light when every *required* check passes:

* the config file parses and every knob is in a sane range,
* the calling window sits inside the legal telemarketing window
  (08:00-21:00 local — the TCPA boundary, with the default 09:00-18:00
  comfortably inside it),
* a suppression list is present and well-formed (an empty file is fine;
  a *missing* file is not — "we forgot the DNC list" must be loud),
* the lead file parses, has unique ids and dialable phones, and
* the campaign timezone resolves.

Each check reports ``name / ok / required / status / detail`` so the result
is equally readable by a human and a pipeline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import CampaignConfig, Lead, SuppressionList, normalize_phone, resolve_tz

LEGAL_EARLIEST_HOUR = 8  # earliest legal local dial hour
LEGAL_LATEST_HOUR = 21  # latest legal local dial hour (exclusive)


@dataclass
class CheckResult:
    name: str
    ok: bool
    status: str
    detail: str = ""
    required: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "required": self.required,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class ReadinessReport:
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return all(c.ok for c in self.checks if c.required)

    def to_dict(self) -> Dict[str, Any]:
        return {"ready": self.ready, "checks": [c.to_dict() for c in self.checks]}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def check_config_values(config: CampaignConfig) -> List[CheckResult]:
    checks: List[CheckResult] = []
    lo, hi = config.window_start_hour, config.window_end_hour

    if not (0 <= lo < hi <= 24):
        checks.append(
            CheckResult(
                "config_window",
                False,
                "invalid_window",
                f"window {lo}:00-{hi}:00 is not a valid ascending hour range",
            )
        )
    elif lo < LEGAL_EARLIEST_HOUR or hi > LEGAL_LATEST_HOUR:
        checks.append(
            CheckResult(
                "config_window",
                False,
                "outside_legal_window",
                f"window {lo}:00-{hi}:00 falls outside the legal "
                f"{LEGAL_EARLIEST_HOUR}:00-{LEGAL_LATEST_HOUR}:00 local window",
            )
        )
    else:
        checks.append(
            CheckResult("config_window", True, "ready", f"{lo}:00-{hi}:00 lead-local")
        )

    problems = []
    if config.slot_minutes <= 0 or config.slot_minutes > 120:
        problems.append(f"slot_minutes={config.slot_minutes}")
    if config.daily_cap <= 0:
        problems.append(f"daily_cap={config.daily_cap}")
    if not (1 <= config.max_attempts <= 10):
        problems.append(f"max_attempts={config.max_attempts}")
    if config.horizon_days <= 0:
        problems.append(f"horizon_days={config.horizon_days}")
    if any(v < 0 for v in config.cooldown_hours.values()):
        problems.append("negative cooldown_hours")
    checks.append(
        CheckResult(
            "config_limits",
            not problems,
            "ready" if not problems else "invalid_limits",
            "caps, attempts, cooldowns in range" if not problems else ", ".join(problems),
        )
    )

    tz_ok = resolve_tz(config.campaign_timezone) is not None
    checks.append(
        CheckResult(
            "campaign_timezone",
            tz_ok,
            "ready" if tz_ok else "unresolvable",
            config.campaign_timezone,
        )
    )
    return checks


def check_suppression_file(path: Optional[Path]) -> Tuple[CheckResult, SuppressionList]:
    empty = SuppressionList()
    if path is None:
        return (
            CheckResult(
                "suppression_list",
                False,
                "missing",
                "no suppression list configured — refusing to dial without a DNC list",
            ),
            empty,
        )
    path = Path(path)
    if not path.exists():
        return (
            CheckResult("suppression_list", False, "missing", f"{path} does not exist"),
            empty,
        )
    text = path.read_text(encoding="utf-8")
    bad = [
        line.strip()
        for line in text.splitlines()
        if line.split("#", 1)[0].strip() and not normalize_phone(line.split("#", 1)[0])
    ]
    sup = SuppressionList.from_text(text)
    if bad:
        return (
            CheckResult(
                "suppression_list",
                False,
                "malformed_entries",
                f"{len(bad)} unparsable entr(y/ies), e.g. {bad[0]!r}",
            ),
            sup,
        )
    return (
        CheckResult(
            "suppression_list", True, "ready", f"{len(sup)} suppressed number(s)"
        ),
        sup,
    )


def check_leads(leads: List[Lead]) -> List[CheckResult]:
    checks: List[CheckResult] = []
    if not leads:
        checks.append(CheckResult("leads", False, "empty", "no leads to work"))
        return checks

    ids = [lead.lead_id for lead in leads]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    missing_phone = [lead.lead_id for lead in leads if not lead.phone_key]
    ok = not dupes and not missing_phone
    detail = f"{len(leads)} lead(s)"
    status = "ready"
    if dupes:
        status, detail = "duplicate_ids", f"duplicate lead ids: {', '.join(dupes[:5])}"
    elif missing_phone:
        status = "missing_phones"
        detail = f"{len(missing_phone)} lead(s) without a dialable phone"
    checks.append(CheckResult("leads", ok, status, detail))

    unknown_tz = sorted(
        {lead.timezone for lead in leads if lead.timezone and resolve_tz(lead.timezone) is None}
    )
    no_tz = sum(1 for lead in leads if not lead.timezone)
    if unknown_tz:
        checks.append(
            CheckResult(
                "lead_timezones",
                False,
                "unresolvable_zones",
                f"unresolvable timezone(s): {', '.join(unknown_tz[:5])}",
                required=False,
            )
        )
    elif no_tz:
        checks.append(
            CheckResult(
                "lead_timezones",
                True,
                "dual_clock_fallback",
                f"{no_tz} lead(s) without a timezone will use the conservative "
                "dual-clock (ET+PT) rule",
                required=False,
            )
        )
    else:
        checks.append(
            CheckResult("lead_timezones", True, "ready", "all lead timezones resolve",
                        required=False)
        )
    return checks


def run_readiness(
    config_path: Path,
    leads_path: Path,
    suppression_path: Optional[Path],
) -> ReadinessReport:
    """File-level pre-flight. Never raises: every failure becomes a check."""
    report = ReadinessReport()

    config: Optional[CampaignConfig] = None
    try:
        config = CampaignConfig.from_dict(_load_json(Path(config_path)))
        report.checks.append(CheckResult("config_file", True, "ready", str(config_path)))
    except Exception as exc:
        report.checks.append(
            CheckResult("config_file", False, "unreadable", f"{type(exc).__name__}: {exc}")
        )
    if config is not None:
        report.checks.extend(check_config_values(config))

    sup_check, _ = check_suppression_file(
        Path(suppression_path) if suppression_path else None
    )
    report.checks.append(sup_check)

    try:
        raw = _load_json(Path(leads_path))
        leads = [Lead.from_dict(item) for item in raw]
        report.checks.append(
            CheckResult("leads_file", True, "ready", str(leads_path))
        )
        report.checks.extend(check_leads(leads))
    except Exception as exc:
        report.checks.append(
            CheckResult("leads_file", False, "unreadable", f"{type(exc).__name__}: {exc}")
        )

    return report
