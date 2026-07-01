"""Win-back ranking: which dormant leads are worth dialing first.

The score is a weighted blend of four explainable factors, each in ``[0, 1]``:

recency      Dormancy sweet spot. A lead that went cold 1-12 months ago is far
             more reactivatable than one contacted last week (not dormant yet)
             or three years ago (stale, number churn, moved away).
engagement   Prior relationship strength: completed jobs, whether they ever
             responded, whether they originally reached out themselves.
value        Job value band (``high`` / ``mid`` / ``low``) — a furnace swap is
             worth more dial priority than a filter change.
seasonality  Service-demand hint by calendar month (HVAC peaks in summer and
             winter, roofing in spring/fall, and so on).

Every ranked lead carries a factor breakdown so the ordering is auditable —
"why is this lead #1" should never require reading the code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .models import Lead

WEIGHTS = {
    "recency": 0.35,
    "engagement": 0.30,
    "value": 0.25,
    "seasonality": 0.10,
}

# (max_days_dormant, factor) — first band whose ceiling covers the value wins.
_RECENCY_BANDS = (
    (30, 0.20),   # too fresh: not actually dormant, deprioritize
    (90, 1.00),   # the sweet spot
    (180, 0.90),
    (365, 0.70),
    (540, 0.50),
    (730, 0.30),
)
_RECENCY_FLOOR = 0.10  # older than 2 years
_RECENCY_UNKNOWN = 0.40

_VALUE_FACTORS = {"high": 1.0, "mid": 0.6, "low": 0.3}
_VALUE_UNKNOWN = 0.4

# service -> months where demand (and answer rates) run hot.
_SEASON_PEAKS: Dict[str, frozenset] = {
    "hvac": frozenset({1, 5, 6, 7, 8, 11, 12}),
    "roofing": frozenset({3, 4, 5, 6, 9, 10}),
    "landscaping": frozenset({3, 4, 5, 6, 7, 8, 9}),
    "lawn care": frozenset({3, 4, 5, 6, 7, 8, 9}),
    "pool": frozenset({3, 4, 5, 6, 7}),
    "gutters": frozenset({3, 4, 9, 10, 11}),
    "pest control": frozenset({4, 5, 6, 7, 8, 9}),
}
_SEASON_PEAK = 1.0
_SEASON_OFF = 0.5
_SEASON_NEUTRAL = 0.7  # service with no seasonal profile (plumbing, electrical…)


@dataclass
class Factor:
    """One scored component with its weight and a human-readable reason."""

    name: str
    value: float
    weight: float
    detail: str

    @property
    def weighted(self) -> float:
        return self.value * self.weight

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": round(self.value, 3),
            "weight": self.weight,
            "weighted": round(self.weighted, 4),
            "detail": self.detail,
        }


@dataclass
class RankedLead:
    lead: Lead
    score: float
    factors: List[Factor] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lead_id": self.lead.lead_id,
            "name": self.lead.name,
            "score": self.score,
            "factors": [f.to_dict() for f in self.factors],
        }


def _days_dormant(lead: Lead, today: date) -> Optional[int]:
    if lead.last_contact is None:
        return None
    return (today - lead.last_contact).days


def recency_factor(lead: Lead, today: date) -> Factor:
    days = _days_dormant(lead, today)
    if days is None:
        return Factor("recency", _RECENCY_UNKNOWN, WEIGHTS["recency"], "last contact unknown")
    for ceiling, value in _RECENCY_BANDS:
        if days <= ceiling:
            return Factor(
                "recency", value, WEIGHTS["recency"], f"{days}d since last contact"
            )
    return Factor(
        "recency", _RECENCY_FLOOR, WEIGHTS["recency"], f"{days}d since last contact (stale)"
    )


def engagement_factor(lead: Lead) -> Factor:
    value = 0.2
    parts = []
    jobs = max(0, int(lead.prior_jobs))
    if jobs:
        value += 0.2 * min(jobs, 3)
        parts.append(f"{jobs} prior job(s)")
    if lead.responded_before:
        value += 0.2
        parts.append("responded before")
    if lead.inbound_inquiry:
        value += 0.2
        parts.append("originally inbound")
    value = min(value, 1.0)
    return Factor(
        "engagement", value, WEIGHTS["engagement"], ", ".join(parts) or "no prior signals"
    )


def value_factor(lead: Lead) -> Factor:
    band = (lead.job_value_band or "").lower()
    value = _VALUE_FACTORS.get(band, _VALUE_UNKNOWN)
    detail = f"job value band: {band}" if band in _VALUE_FACTORS else "job value band unknown"
    return Factor("value", value, WEIGHTS["value"], detail)


def seasonality_factor(lead: Lead, today: date) -> Factor:
    service = (lead.service or "").lower()
    peaks = _SEASON_PEAKS.get(service)
    if peaks is None:
        return Factor(
            "seasonality",
            _SEASON_NEUTRAL,
            WEIGHTS["seasonality"],
            f"no seasonal profile for '{service or 'unknown'}'",
        )
    if today.month in peaks:
        return Factor(
            "seasonality", _SEASON_PEAK, WEIGHTS["seasonality"], f"{service} in-season"
        )
    return Factor(
        "seasonality", _SEASON_OFF, WEIGHTS["seasonality"], f"{service} off-season"
    )


def score_lead(lead: Lead, now: Optional[datetime] = None) -> RankedLead:
    """Score one lead 0-100 with a full factor breakdown."""
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    factors = [
        recency_factor(lead, today),
        engagement_factor(lead),
        value_factor(lead),
        seasonality_factor(lead, today),
    ]
    raw = sum(f.weighted for f in factors)  # weights sum to 1.0 -> raw in [0, 1]
    return RankedLead(lead=lead, score=round(raw * 100, 1), factors=factors)


def rank_leads(leads: Sequence[Lead], now: Optional[datetime] = None) -> List[RankedLead]:
    """Rank leads by reactivation potential. Deterministic: score desc, then id."""
    ranked = [score_lead(lead, now=now) for lead in leads]
    ranked.sort(key=lambda r: (-r.score, r.lead.lead_id))
    return ranked
