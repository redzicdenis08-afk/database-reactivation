"""Compliant call scheduling: right lead, right number, right local time.

Hard rules, enforced in this order for every lead:

1. **Suppression** — a number on the suppression list is never scheduled.
2. **Terminal / in-flight states** — booked, opted-out, exhausted and
   currently-attempted leads are never scheduled.
3. **Attempt cap** — leads at or over ``max_attempts`` are skipped.
4. **Cooldown** — a lead is never dialed before its ``cooldown_until``.
5. **Calling window** — every slot must fall inside the lead's *own local*
   calling window (``window_start_hour`` inclusive, ``window_end_hour``
   exclusive). If the lead's timezone is unknown, the slot must be legal in
   **both** US Eastern and US Pacific — the conservative dual-clock rule, so
   an unmapped number can never be dialed too early or too late anywhere in
   the continental US.
6. **Daily cap** — at most ``daily_cap`` calls per campaign-timezone day.

The output is deterministic: same leads + config + clock in, same schedule
out. Leads are ranked first (see :mod:`dbreactivation.ranking`) and greedily
assigned the earliest legal slot, one call per slot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from .models import (
    CALLABLE_STATES,
    CampaignConfig,
    Lead,
    SuppressionList,
    ensure_utc,
    iso_utc,
    resolve_tz,
)
from .ranking import rank_leads

_DUAL_CLOCK_ZONES = ("America/New_York", "America/Los_Angeles")


@dataclass
class ScheduledCall:
    lead_id: str
    phone: str
    attempt_number: int
    score: float
    scheduled_at: datetime  # aware UTC
    lead_local: str  # what the lead's clock reads at dial time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lead_id": self.lead_id,
            "phone": self.phone,
            "attempt_number": self.attempt_number,
            "score": self.score,
            "scheduled_at": iso_utc(self.scheduled_at),
            "lead_local": self.lead_local,
        }


@dataclass
class SkippedLead:
    lead_id: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {"lead_id": self.lead_id, "reason": self.reason}


@dataclass
class Schedule:
    generated_at: datetime
    calls: List[ScheduledCall] = field(default_factory=list)
    skipped: List[SkippedLead] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": iso_utc(self.generated_at),
            "calls": [c.to_dict() for c in self.calls],
            "skipped": [s.to_dict() for s in self.skipped],
        }


def in_calling_window(lead: Lead, when: datetime, config: CampaignConfig) -> bool:
    """True when ``when`` is inside the lead's local calling window.

    Unknown timezone -> conservative dual-clock rule: must be inside the
    window in both US Eastern and US Pacific simultaneously.
    """
    when = ensure_utc(when)
    lo, hi = config.window_start_hour, config.window_end_hour
    tz = resolve_tz(lead.timezone)
    if tz is not None:
        return lo <= when.astimezone(tz).hour < hi
    for zone_name in _DUAL_CLOCK_ZONES:
        zone = resolve_tz(zone_name)
        if zone is None or not (lo <= when.astimezone(zone).hour < hi):
            return False
    return True


def _align_up(when: datetime, slot_minutes: int) -> datetime:
    """Round ``when`` up to the next slot boundary (aligned to the hour)."""
    when = ensure_utc(when).replace(second=0, microsecond=0)
    remainder = when.minute % slot_minutes
    if remainder:
        when += timedelta(minutes=slot_minutes - remainder)
    return when


def _lead_local_label(lead: Lead, when: datetime) -> str:
    tz = resolve_tz(lead.timezone)
    if tz is None:
        return ensure_utc(when).strftime("%Y-%m-%d %H:%M UTC (tz unknown)")
    local = ensure_utc(when).astimezone(tz)
    return local.strftime("%Y-%m-%d %H:%M ") + (lead.timezone or "UTC")


def build_schedule(
    leads: Sequence[Lead],
    config: CampaignConfig,
    suppression: Optional[SuppressionList] = None,
    now: Optional[datetime] = None,
) -> Schedule:
    """Assign each eligible lead the earliest legal dial slot.

    Leads are processed in rank order; each takes the first free slot that
    satisfies every compliance rule. Ineligible leads land in
    ``schedule.skipped`` with an explicit reason.
    """
    now = ensure_utc(now or datetime.now(timezone.utc))
    if suppression is None:  # NB: an empty SuppressionList is falsy — check identity
        suppression = SuppressionList()
    campaign_tz = resolve_tz(config.campaign_timezone) or timezone.utc
    horizon_end = now + timedelta(days=config.horizon_days)
    slot = timedelta(minutes=config.slot_minutes)

    schedule = Schedule(generated_at=now)
    occupied: set = set()
    day_counts: Dict[str, int] = {}

    for ranked in rank_leads(leads, now=now):
        lead = ranked.lead
        if lead.phone_key == "":
            schedule.skipped.append(SkippedLead(lead.lead_id, "invalid_phone"))
            continue
        if lead.phone in suppression:
            schedule.skipped.append(SkippedLead(lead.lead_id, "suppressed"))
            continue
        if lead.state not in CALLABLE_STATES:
            schedule.skipped.append(
                SkippedLead(lead.lead_id, f"state_not_callable:{lead.state.value}")
            )
            continue
        if lead.attempts >= config.max_attempts:
            schedule.skipped.append(SkippedLead(lead.lead_id, "attempt_cap_reached"))
            continue

        earliest = now
        if lead.cooldown_until is not None:
            earliest = max(earliest, ensure_utc(lead.cooldown_until))
        candidate = _align_up(earliest, config.slot_minutes)

        placed = False
        while candidate <= horizon_end:
            day_key = candidate.astimezone(campaign_tz).strftime("%Y-%m-%d")
            if (
                candidate not in occupied
                and day_counts.get(day_key, 0) < config.daily_cap
                and in_calling_window(lead, candidate, config)
            ):
                occupied.add(candidate)
                day_counts[day_key] = day_counts.get(day_key, 0) + 1
                schedule.calls.append(
                    ScheduledCall(
                        lead_id=lead.lead_id,
                        phone=lead.phone,
                        attempt_number=lead.attempts + 1,
                        score=ranked.score,
                        scheduled_at=candidate,
                        lead_local=_lead_local_label(lead, candidate),
                    )
                )
                placed = True
                break
            candidate += slot
        if not placed:
            schedule.skipped.append(SkippedLead(lead.lead_id, "no_capacity_in_horizon"))

    schedule.calls.sort(key=lambda c: (c.scheduled_at, c.lead_id))
    return schedule
