"""Core data model for the database-reactivation engine.

Everything downstream (ranking, scheduling, lifecycle, intake, readiness)
works off these types. Pure standard library, no I/O side effects.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Lead lifecycle states
# ---------------------------------------------------------------------------


class LeadState(str, Enum):
    """Lifecycle of a dormant lead inside a reactivation campaign."""

    DORMANT = "dormant"
    QUEUED = "queued"
    ATTEMPTED = "attempted"
    REACHED = "reached"
    VOICEMAIL = "voicemail"
    NO_ANSWER = "no_answer"
    INTERESTED = "interested"
    BOOKED = "booked"
    OPTED_OUT = "opted_out"
    EXHAUSTED = "exhausted"


TERMINAL_STATES = frozenset(
    {LeadState.BOOKED, LeadState.OPTED_OUT, LeadState.EXHAUSTED}
)

#: States a scheduler is allowed to dial from.
CALLABLE_STATES = frozenset(
    {
        LeadState.DORMANT,
        LeadState.QUEUED,
        LeadState.VOICEMAIL,
        LeadState.NO_ANSWER,
        LeadState.REACHED,
        LeadState.INTERESTED,
    }
)


class CallOutcome(str, Enum):
    """What actually happened on a single dial."""

    REACHED = "reached"
    VOICEMAIL = "voicemail"
    NO_ANSWER = "no_answer"
    FAILED = "failed"


JOB_VALUE_BANDS = ("low", "mid", "high")

# ---------------------------------------------------------------------------
# Phone + timezone helpers
# ---------------------------------------------------------------------------

_DIGITS = re.compile(r"\d")


def normalize_phone(raw: Any) -> str:
    """Reduce a phone number to its last 10 digits for stable comparison.

    ``+1 (555) 010-4242`` and ``5550104242`` normalize to the same key.
    Returns ``""`` when fewer than 7 digits are present (not a dialable number).
    """
    digits = "".join(_DIGITS.findall(str(raw or "")))
    if len(digits) < 7:
        return ""
    return digits[-10:]


# Fixed standard-time offsets used when the OS has no tz database (a real
# production failure mode on bare Windows hosts). Offsets are standard time,
# which is the *conservative* direction for calling windows.
_FIXED_OFFSET_HOURS = {
    "UTC": 0,
    "America/New_York": -5,
    "America/Chicago": -6,
    "America/Denver": -7,
    "America/Phoenix": -7,
    "America/Los_Angeles": -8,
    "America/Anchorage": -9,
    "Pacific/Honolulu": -10,
}


def resolve_tz(name: Optional[str]):
    """Resolve an IANA zone name to a tzinfo, or ``None`` if unknown.

    Tries :mod:`zoneinfo` first; falls back to a fixed standard-time offset
    for common US zones so the engine keeps working on hosts without tzdata.
    """
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        offset = _FIXED_OFFSET_HOURS.get(name)
        if offset is None:
            return None
        return timezone(timedelta(hours=offset), name)


def ensure_utc(dt: datetime) -> datetime:
    """Interpret naive datetimes as UTC; convert aware ones to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_when(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (with optional trailing ``Z``) to aware UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return ensure_utc(datetime.fromisoformat(text))


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return ensure_utc(dt).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Lead
# ---------------------------------------------------------------------------


@dataclass
class Lead:
    """One dormant lead pulled from a client's old-customer list."""

    lead_id: str
    phone: str
    name: str = ""
    timezone: str = ""  # IANA name; empty means unknown (dual-clock rule)
    last_contact: Optional[date] = None
    prior_jobs: int = 0
    responded_before: bool = False
    inbound_inquiry: bool = False
    job_value_band: str = ""  # "low" | "mid" | "high" | "" (unknown)
    service: str = ""  # e.g. "hvac", "roofing"
    state: LeadState = LeadState.DORMANT
    attempts: int = 0
    last_attempt_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    history: List[Dict[str, str]] = field(default_factory=list)

    @property
    def phone_key(self) -> str:
        return normalize_phone(self.phone)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Lead":
        last_contact = data.get("last_contact") or None
        if isinstance(last_contact, str):
            last_contact = date.fromisoformat(last_contact)
        state = data.get("state") or LeadState.DORMANT
        return cls(
            lead_id=str(data.get("lead_id") or data.get("id") or ""),
            phone=str(data.get("phone") or ""),
            name=str(data.get("name") or ""),
            timezone=str(data.get("timezone") or ""),
            last_contact=last_contact,
            prior_jobs=int(data.get("prior_jobs") or 0),
            responded_before=bool(data.get("responded_before")),
            inbound_inquiry=bool(data.get("inbound_inquiry")),
            job_value_band=str(data.get("job_value_band") or "").lower(),
            service=str(data.get("service") or "").lower(),
            state=LeadState(state),
            attempts=int(data.get("attempts") or 0),
            last_attempt_at=parse_when(data.get("last_attempt_at")),
            cooldown_until=parse_when(data.get("cooldown_until")),
            history=list(data.get("history") or []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lead_id": self.lead_id,
            "phone": self.phone,
            "name": self.name,
            "timezone": self.timezone,
            "last_contact": self.last_contact.isoformat() if self.last_contact else None,
            "prior_jobs": self.prior_jobs,
            "responded_before": self.responded_before,
            "inbound_inquiry": self.inbound_inquiry,
            "job_value_band": self.job_value_band,
            "service": self.service,
            "state": self.state.value,
            "attempts": self.attempts,
            "last_attempt_at": iso_utc(self.last_attempt_at),
            "cooldown_until": iso_utc(self.cooldown_until),
            "history": self.history,
        }


# ---------------------------------------------------------------------------
# Campaign configuration
# ---------------------------------------------------------------------------

DEFAULT_COOLDOWN_HOURS = {
    "no_answer": 4,
    "voicemail": 24,
    "failed": 24,
    "reached": 72,
}


@dataclass
class CampaignConfig:
    """Knobs for one reactivation campaign. All times are lead-local hours."""

    campaign_timezone: str = "America/New_York"
    window_start_hour: int = 9  # inclusive, lead-local
    window_end_hour: int = 18  # exclusive, lead-local
    slot_minutes: int = 15
    daily_cap: int = 50
    max_attempts: int = 4
    horizon_days: int = 3
    cooldown_hours: Dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_COOLDOWN_HOURS)
    )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CampaignConfig":
        cooldowns = dict(DEFAULT_COOLDOWN_HOURS)
        cooldowns.update(
            {str(k): int(v) for k, v in (data.get("cooldown_hours") or {}).items()}
        )
        return cls(
            campaign_timezone=str(data.get("campaign_timezone") or "America/New_York"),
            window_start_hour=int(data.get("window_start_hour", 9)),
            window_end_hour=int(data.get("window_end_hour", 18)),
            slot_minutes=int(data.get("slot_minutes", 15)),
            daily_cap=int(data.get("daily_cap", 50)),
            max_attempts=int(data.get("max_attempts", 4)),
            horizon_days=int(data.get("horizon_days", 3)),
            cooldown_hours=cooldowns,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "campaign_timezone": self.campaign_timezone,
            "window_start_hour": self.window_start_hour,
            "window_end_hour": self.window_end_hour,
            "slot_minutes": self.slot_minutes,
            "daily_cap": self.daily_cap,
            "max_attempts": self.max_attempts,
            "horizon_days": self.horizon_days,
            "cooldown_hours": dict(self.cooldown_hours),
        }

    def cooldown_for(self, outcome: str) -> timedelta:
        return timedelta(hours=int(self.cooldown_hours.get(outcome, 0)))


# ---------------------------------------------------------------------------
# Suppression list
# ---------------------------------------------------------------------------


class SuppressionList:
    """A set of permanently-suppressed phone numbers (DNC / opt-out)."""

    def __init__(self, numbers: Optional[Any] = None) -> None:
        self._numbers = set()
        for raw in numbers or []:
            self.add(raw)

    def add(self, raw: Any) -> bool:
        key = normalize_phone(raw)
        if not key:
            return False
        if key in self._numbers:
            return False
        self._numbers.add(key)
        return True

    def __contains__(self, raw: Any) -> bool:
        return normalize_phone(raw) in self._numbers

    def __len__(self) -> int:
        return len(self._numbers)

    def numbers(self) -> List[str]:
        return sorted(self._numbers)

    @classmethod
    def from_text(cls, text: str) -> "SuppressionList":
        """Parse one number per line; ``#`` starts a comment."""
        sup = cls()
        for line in text.splitlines():
            entry = line.split("#", 1)[0].strip()
            if entry:
                sup.add(entry)
        return sup

    def to_text(self) -> str:
        return "\n".join(self.numbers()) + ("\n" if self._numbers else "")
