"""Campaign state machine: legal lead-lifecycle transitions, enforced.

The one rule that matters most commercially and legally: ``OPTED_OUT`` is a
one-way door. Nothing transitions out of it, ever. ``BOOKED`` and
``EXHAUSTED`` are likewise terminal.

::

    dormant ──> queued ──> attempted ──> reached ────> interested ──> booked
                   ^            │            │  │            │
                   │            ├─> voicemail│  └─> queued   ├─> opted_out
                   │            └─> no_answer│               └─> exhausted
                   │                 │  │    └─> opted_out / exhausted
                   └─────────────────┘  └─> exhausted
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, FrozenSet, Optional

from .models import TERMINAL_STATES, Lead, LeadState, iso_utc


class LifecycleError(ValueError):
    """Raised on an illegal lead-state transition."""


TRANSITIONS: Dict[LeadState, FrozenSet[LeadState]] = {
    LeadState.DORMANT: frozenset({LeadState.QUEUED}),
    LeadState.QUEUED: frozenset({LeadState.ATTEMPTED}),
    LeadState.ATTEMPTED: frozenset(
        {LeadState.REACHED, LeadState.VOICEMAIL, LeadState.NO_ANSWER}
    ),
    LeadState.REACHED: frozenset(
        {
            LeadState.INTERESTED,
            LeadState.OPTED_OUT,
            LeadState.QUEUED,  # asked to be called back later
            LeadState.EXHAUSTED,
        }
    ),
    LeadState.VOICEMAIL: frozenset(
        {LeadState.QUEUED, LeadState.ATTEMPTED, LeadState.EXHAUSTED, LeadState.OPTED_OUT}
    ),
    LeadState.NO_ANSWER: frozenset(
        {LeadState.QUEUED, LeadState.ATTEMPTED, LeadState.EXHAUSTED}
    ),
    LeadState.INTERESTED: frozenset(
        {LeadState.BOOKED, LeadState.QUEUED, LeadState.OPTED_OUT}
    ),
    # Terminal: no way out.
    LeadState.BOOKED: frozenset(),
    LeadState.OPTED_OUT: frozenset(),
    LeadState.EXHAUSTED: frozenset(),
}


def is_terminal(state: LeadState) -> bool:
    return state in TERMINAL_STATES


def can_transition(current: LeadState, new: LeadState) -> bool:
    return new in TRANSITIONS.get(current, frozenset())


def transition(
    lead: Lead,
    new_state: LeadState,
    at: Optional[datetime] = None,
    note: str = "",
) -> Lead:
    """Move ``lead`` to ``new_state``, or raise :class:`LifecycleError`.

    Appends an audit entry to ``lead.history`` so every state change stays
    traceable after the fact.
    """
    current = lead.state
    if not can_transition(current, new_state):
        if is_terminal(current):
            raise LifecycleError(
                f"{lead.lead_id}: '{current.value}' is terminal; cannot move to "
                f"'{new_state.value}'"
            )
        raise LifecycleError(
            f"{lead.lead_id}: illegal transition '{current.value}' -> '{new_state.value}'"
        )
    lead.history.append(
        {
            "at": iso_utc(at or datetime.now(timezone.utc)) or "",
            "from": current.value,
            "to": new_state.value,
            "note": note,
        }
    )
    lead.state = new_state
    return lead
