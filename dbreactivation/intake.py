"""Outcome intake: turn raw call results back into campaign state.

Feed it a JSON list of call records — either with an explicit ``outcome``
field or with a raw ``transcript`` to classify — and it:

* advances each lead through the state machine (attempt -> outcome),
* increments attempt counters and stamps ``last_attempt_at``,
* sets per-outcome cooldowns (voicemail backs off longer than no-answer),
* adds opt-outs to the suppression list **permanently**, and
* marks leads that hit the attempt cap as ``exhausted``.

Classification scans only what the *customer* said where transcripts carry
roles — scanning agent lines too would match the agent's own compliance
script ("if you'd like to be removed…") on every call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .lifecycle import can_transition, transition
from .models import (
    CallOutcome,
    CampaignConfig,
    Lead,
    LeadState,
    SuppressionList,
    ensure_utc,
    iso_utc,
    parse_when,
)


def _compile(patterns: Iterable[str]) -> List["re.Pattern[str]"]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


VOICEMAIL_PATTERNS = _compile(
    [
        r"\bleave (?:a|your) message\b",
        r"\bafter the (?:tone|beep)\b",
        r"\bvoice ?mail\b",
        r"\bmailbox\b",
        r"\bpress \d\b",
        r"\bthank you for calling\b",
    ]
)

OPT_OUT_PATTERNS = _compile(
    [
        r"\bdo not call\b",
        r"\bdon'?t call\b",
        r"\bstop calling\b",
        r"\bremove (?:me|us|my number)\b",
        r"\btake (?:me|us) off\b",
        r"\bunsubscribe\b",
    ]
)

BOOKED_PATTERNS = _compile(
    [
        r"\bbook (?:me|it|us) in\b",
        r"\bbook (?:the|an) appointment\b",
        r"\bsee you (?:then|on)\b",
        r"\bthat time works\b",
        r"\bconfirm(?:ed)? (?:the|my|that) appointment\b",
    ]
)

INTERESTED_PATTERNS = _compile(
    [
        r"\binterested\b",
        r"\bsend (?:me|us) (?:the|more) (?:details|info)\b",
        r"\bcall (?:me|us) back\b",
        r"\bhow much\b",
        r"\bwhat would (?:it|that) cost\b",
    ]
)


def _customer_text(transcript: Any) -> str:
    """Extract the customer side of a transcript.

    Accepts a plain string (used as-is) or a list of ``{"role": ..,
    "text"/"message": ..}`` turns, in which case only non-agent turns count.
    """
    if transcript is None:
        return ""
    if isinstance(transcript, str):
        return transcript
    parts: List[str] = []
    for turn in transcript:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").lower()
        if role in {"agent", "assistant", "bot", "ai", "system"}:
            continue
        parts.append(str(turn.get("text") or turn.get("message") or ""))
    return " ".join(parts)


def _matches(patterns: Sequence["re.Pattern[str]"], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def classify_transcript(transcript: Any) -> Tuple[CallOutcome, Dict[str, bool]]:
    """Classify a transcript into an outcome plus intent signals."""
    text = _customer_text(transcript).strip()
    signals = {"opted_out": False, "interested": False, "booked": False}
    if not text:
        return CallOutcome.NO_ANSWER, signals
    if _matches(VOICEMAIL_PATTERNS, text):
        return CallOutcome.VOICEMAIL, signals
    signals["opted_out"] = _matches(OPT_OUT_PATTERNS, text)
    signals["booked"] = _matches(BOOKED_PATTERNS, text)
    signals["interested"] = signals["booked"] or _matches(INTERESTED_PATTERNS, text)
    return CallOutcome.REACHED, signals


@dataclass
class OutcomeRecord:
    """One completed call, as reported by the dialer/voice platform."""

    lead_id: str
    at: datetime
    outcome: Optional[CallOutcome] = None  # explicit outcome wins over transcript
    transcript: Any = None
    call_id: str = ""
    opted_out: bool = False
    interested: bool = False
    booked: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OutcomeRecord":
        outcome = data.get("outcome")
        return cls(
            lead_id=str(data.get("lead_id") or ""),
            at=parse_when(data.get("at")) or datetime.now(timezone.utc),
            outcome=CallOutcome(outcome) if outcome else None,
            transcript=data.get("transcript"),
            call_id=str(data.get("call_id") or ""),
            opted_out=bool(data.get("opted_out")),
            interested=bool(data.get("interested")),
            booked=bool(data.get("booked")),
        )

    def resolve(self) -> Tuple[CallOutcome, Dict[str, bool]]:
        """Merge explicit fields with transcript classification."""
        outcome, signals = classify_transcript(self.transcript)
        if self.outcome is not None:
            outcome = self.outcome
        signals["opted_out"] = signals["opted_out"] or self.opted_out
        signals["booked"] = signals["booked"] or self.booked
        signals["interested"] = (
            signals["interested"] or self.interested or signals["booked"]
        )
        return outcome, signals


@dataclass
class IngestReport:
    applied: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    suppression_additions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "applied": self.applied,
            "errors": self.errors,
            "suppression_additions": self.suppression_additions,
        }


_OUTCOME_STATE = {
    CallOutcome.REACHED: LeadState.REACHED,
    CallOutcome.VOICEMAIL: LeadState.VOICEMAIL,
    CallOutcome.NO_ANSWER: LeadState.NO_ANSWER,
    CallOutcome.FAILED: LeadState.NO_ANSWER,
}


def _route_to_attempted(lead: Lead, at: datetime, note: str) -> None:
    """Walk the lead to ATTEMPTED via whatever legal path its state allows."""
    if lead.state == LeadState.ATTEMPTED:
        return
    if can_transition(lead.state, LeadState.ATTEMPTED):
        transition(lead, LeadState.ATTEMPTED, at=at, note=note)
        return
    if can_transition(lead.state, LeadState.QUEUED):
        transition(lead, LeadState.QUEUED, at=at, note=note)
    transition(lead, LeadState.ATTEMPTED, at=at, note=note)


def apply_outcome(
    lead: Lead,
    record: OutcomeRecord,
    config: CampaignConfig,
    suppression: SuppressionList,
) -> Dict[str, Any]:
    """Apply one call outcome to one lead. Returns an audit summary."""
    outcome, signals = record.resolve()
    at = ensure_utc(record.at)
    note = f"call {record.call_id}".strip()

    _route_to_attempted(lead, at, note)
    lead.attempts += 1
    lead.last_attempt_at = at
    transition(lead, _OUTCOME_STATE[outcome], at=at, note=note)

    if outcome == CallOutcome.REACHED:
        if signals["opted_out"]:
            transition(lead, LeadState.OPTED_OUT, at=at, note="customer opt-out")
            suppression.add(lead.phone)
            lead.cooldown_until = None
        elif signals["interested"]:
            transition(lead, LeadState.INTERESTED, at=at, note=note)
            if signals["booked"]:
                transition(lead, LeadState.BOOKED, at=at, note=note)

    if lead.state not in {LeadState.BOOKED, LeadState.OPTED_OUT}:
        cooldown = config.cooldown_for(outcome.value)
        lead.cooldown_until = at + cooldown if cooldown else None
        if lead.attempts >= config.max_attempts and can_transition(
            lead.state, LeadState.EXHAUSTED
        ):
            transition(lead, LeadState.EXHAUSTED, at=at, note="attempt cap reached")

    return {
        "lead_id": lead.lead_id,
        "call_id": record.call_id,
        "outcome": outcome.value,
        "state": lead.state.value,
        "attempts": lead.attempts,
        "cooldown_until": iso_utc(lead.cooldown_until),
        "opted_out": signals["opted_out"],
        "interested": signals["interested"],
        "booked": signals["booked"],
    }


def ingest_outcomes(
    leads: Sequence[Lead],
    records: Sequence[OutcomeRecord],
    config: CampaignConfig,
    suppression: Optional[SuppressionList] = None,
) -> IngestReport:
    """Apply a batch of outcomes; records are processed in timestamp order."""
    if suppression is None:  # NB: an empty SuppressionList is falsy — check identity
        suppression = SuppressionList()
    by_id = {lead.lead_id: lead for lead in leads}
    report = IngestReport()
    before = set(suppression.numbers())

    for record in sorted(records, key=lambda r: (ensure_utc(r.at), r.lead_id)):
        lead = by_id.get(record.lead_id)
        if lead is None:
            report.errors.append(
                {"lead_id": record.lead_id, "error": "unknown_lead"}
            )
            continue
        try:
            report.applied.append(apply_outcome(lead, record, config, suppression))
        except Exception as exc:  # keep the batch going; report the failure
            report.errors.append(
                {"lead_id": record.lead_id, "error": f"{type(exc).__name__}: {exc}"}
            )

    report.suppression_additions = sorted(set(suppression.numbers()) - before)
    return report
