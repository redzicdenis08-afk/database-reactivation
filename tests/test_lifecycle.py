"""Lifecycle state machine: legal transitions only, terminal means terminal."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dbreactivation import (
    TRANSITIONS,
    Lead,
    LeadState,
    LifecycleError,
    can_transition,
    is_terminal,
    transition,
)

AT = datetime(2026, 7, 2, 15, 0, tzinfo=timezone.utc)


def make_lead(state: LeadState = LeadState.DORMANT) -> Lead:
    return Lead(lead_id="L-1", phone="+1-555-0101", state=state)


def test_happy_path_dormant_to_booked():
    lead = make_lead()
    for state in (
        LeadState.QUEUED,
        LeadState.ATTEMPTED,
        LeadState.REACHED,
        LeadState.INTERESTED,
        LeadState.BOOKED,
    ):
        transition(lead, state, at=AT)
    assert lead.state == LeadState.BOOKED
    assert len(lead.history) == 5


def test_illegal_shortcut_raises():
    lead = make_lead()
    with pytest.raises(LifecycleError):
        transition(lead, LeadState.BOOKED, at=AT)
    assert lead.state == LeadState.DORMANT  # unchanged on failure
    assert lead.history == []


def test_opted_out_is_a_one_way_door():
    lead = make_lead(LeadState.OPTED_OUT)
    for target in LeadState:
        assert not can_transition(LeadState.OPTED_OUT, target)
    with pytest.raises(LifecycleError, match="terminal"):
        transition(lead, LeadState.QUEUED, at=AT)


def test_all_terminal_states_have_no_exits():
    for state in (LeadState.BOOKED, LeadState.OPTED_OUT, LeadState.EXHAUSTED):
        assert is_terminal(state)
        assert TRANSITIONS[state] == frozenset()


def test_voicemail_can_requeue_but_not_book():
    assert can_transition(LeadState.VOICEMAIL, LeadState.QUEUED)
    assert can_transition(LeadState.VOICEMAIL, LeadState.EXHAUSTED)
    assert not can_transition(LeadState.VOICEMAIL, LeadState.BOOKED)


def test_reached_customer_can_ask_for_callback():
    lead = make_lead(LeadState.REACHED)
    transition(lead, LeadState.QUEUED, at=AT, note="asked to call back Friday")
    assert lead.state == LeadState.QUEUED


def test_every_state_is_covered_by_the_transition_table():
    assert set(TRANSITIONS) == set(LeadState)


def test_history_records_the_audit_trail():
    lead = make_lead()
    transition(lead, LeadState.QUEUED, at=AT, note="campaign start")
    entry = lead.history[0]
    assert entry["from"] == "dormant"
    assert entry["to"] == "queued"
    assert entry["note"] == "campaign start"
    assert entry["at"].startswith("2026-07-02T15:00:00")
