"""Outcome intake: classification + feedback into state, cooldowns, suppression."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dbreactivation import (
    CallOutcome,
    CampaignConfig,
    Lead,
    LeadState,
    OutcomeRecord,
    SuppressionList,
    classify_transcript,
    ingest_outcomes,
)

AT = datetime(2026, 7, 2, 15, 0, tzinfo=timezone.utc)


def make_lead(**overrides) -> Lead:
    base = dict(lead_id="L-1", phone="+1-555-0101", timezone="UTC")
    base.update(overrides)
    return Lead(**base)


def record(**overrides) -> OutcomeRecord:
    base = dict(lead_id="L-1", at=AT, call_id="call-1")
    base.update(overrides)
    return OutcomeRecord(**base)


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def test_classify_voicemail():
    outcome, signals = classify_transcript(
        "You've reached Sam's phone, please leave a message after the tone."
    )
    assert outcome == CallOutcome.VOICEMAIL
    assert not any(signals.values())


def test_classify_opt_out():
    outcome, signals = classify_transcript("Please take me off your list, do not call again.")
    assert outcome == CallOutcome.REACHED
    assert signals["opted_out"]


def test_classify_booking_implies_interest():
    outcome, signals = classify_transcript("Sure, that time works, book me in.")
    assert outcome == CallOutcome.REACHED
    assert signals["booked"] and signals["interested"]


def test_classify_empty_transcript_is_no_answer():
    outcome, _ = classify_transcript("")
    assert outcome == CallOutcome.NO_ANSWER


def test_classification_ignores_the_agents_own_compliance_script():
    # The agent says "do not call" as part of its own script; the customer
    # never asks to be removed. Scanning agent lines would false-positive.
    transcript = [
        {"role": "agent", "text": "If you would like us to not call again, just say do not call."},
        {"role": "customer", "text": "No that's fine, I'm actually interested."},
    ]
    outcome, signals = classify_transcript(transcript)
    assert outcome == CallOutcome.REACHED
    assert not signals["opted_out"]
    assert signals["interested"]


# ---------------------------------------------------------------------------
# feedback into lead state
# ---------------------------------------------------------------------------


def test_voicemail_sets_backoff_cooldown():
    lead = make_lead()
    config = CampaignConfig()
    report = ingest_outcomes(
        [lead],
        [record(transcript="Please leave a message after the beep.")],
        config,
        SuppressionList(),
    )
    assert lead.state == LeadState.VOICEMAIL
    assert lead.attempts == 1
    assert lead.last_attempt_at == AT
    assert lead.cooldown_until == AT + timedelta(hours=24)
    assert report.applied[0]["outcome"] == "voicemail"


def test_no_answer_uses_the_shorter_cooldown():
    lead = make_lead()
    ingest_outcomes(
        [lead],
        [record(outcome=CallOutcome.NO_ANSWER)],
        CampaignConfig(),
        SuppressionList(),
    )
    assert lead.state == LeadState.NO_ANSWER
    assert lead.cooldown_until == AT + timedelta(hours=4)


def test_opt_out_goes_straight_to_permanent_suppression():
    lead = make_lead()
    suppression = SuppressionList()
    report = ingest_outcomes(
        [lead],
        [record(transcript="Stop calling this number, remove me from your list.")],
        CampaignConfig(),
        suppression,
    )
    assert lead.state == LeadState.OPTED_OUT
    assert lead.phone in suppression
    assert report.suppression_additions == ["15550101"]
    assert lead.cooldown_until is None  # no retry, ever


def test_booked_lead_lands_in_booked_state():
    lead = make_lead()
    ingest_outcomes(
        [lead],
        [record(transcript="Perfect, book me in, see you then.")],
        CampaignConfig(),
        SuppressionList(),
    )
    assert lead.state == LeadState.BOOKED


def test_explicit_outcome_field_wins_over_transcript():
    lead = make_lead()
    ingest_outcomes(
        [lead],
        [record(outcome=CallOutcome.VOICEMAIL, transcript="I'm interested, how much?")],
        CampaignConfig(),
        SuppressionList(),
    )
    assert lead.state == LeadState.VOICEMAIL


def test_attempt_cap_exhausts_the_lead():
    lead = make_lead(state=LeadState.VOICEMAIL, attempts=3)
    ingest_outcomes(
        [lead],
        [record(outcome=CallOutcome.NO_ANSWER)],
        CampaignConfig(max_attempts=4),
        SuppressionList(),
    )
    assert lead.attempts == 4
    assert lead.state == LeadState.EXHAUSTED


def test_interested_lead_is_never_auto_exhausted():
    lead = make_lead(attempts=3)
    ingest_outcomes(
        [lead],
        [record(transcript="I'm interested, call me back.")],
        CampaignConfig(max_attempts=4),
        SuppressionList(),
    )
    assert lead.attempts == 4
    assert lead.state == LeadState.INTERESTED  # a live opportunity stays live


def test_unknown_lead_is_reported_not_crashed():
    lead = make_lead()
    report = ingest_outcomes(
        [lead],
        [record(lead_id="L-GHOST")],
        CampaignConfig(),
        SuppressionList(),
    )
    assert report.applied == []
    assert report.errors == [{"lead_id": "L-GHOST", "error": "unknown_lead"}]
    assert lead.state == LeadState.DORMANT


def test_records_apply_in_timestamp_order():
    lead = make_lead()
    later = record(call_id="call-2", at=AT + timedelta(hours=26), transcript="I'm interested.")
    earlier = record(call_id="call-1", at=AT, transcript="Leave a message after the tone.")
    report = ingest_outcomes(
        [lead], [later, earlier], CampaignConfig(), SuppressionList()
    )
    assert [row["call_id"] for row in report.applied] == ["call-1", "call-2"]
    assert lead.state == LeadState.INTERESTED
    assert lead.attempts == 2


def test_history_carries_the_full_audit_trail():
    lead = make_lead()
    ingest_outcomes(
        [lead],
        [record(transcript="Leave a message after the tone.")],
        CampaignConfig(),
        SuppressionList(),
    )
    states = [entry["to"] for entry in lead.history]
    assert states == ["queued", "attempted", "voicemail"]
