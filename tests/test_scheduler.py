"""Scheduler: calling windows, quiet hours, caps, cooldowns, suppression."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dbreactivation import (
    CampaignConfig,
    Lead,
    LeadState,
    SuppressionList,
    build_schedule,
    in_calling_window,
)
from dbreactivation.models import resolve_tz

UTC = timezone.utc


def make_lead(**overrides) -> Lead:
    base = dict(
        lead_id="L-1",
        phone="+1-555-0101",
        timezone="UTC",
        job_value_band="mid",
    )
    base.update(overrides)
    return Lead(**base)


def utc_config(**overrides) -> CampaignConfig:
    base = dict(campaign_timezone="UTC", window_start_hour=9, window_end_hour=18)
    base.update(overrides)
    return CampaignConfig(**base)


# ---------------------------------------------------------------------------
# quiet hours / window edges
# ---------------------------------------------------------------------------


def test_never_dials_before_window_opens():
    now = datetime(2026, 7, 2, 6, 0, tzinfo=UTC)  # 06:00 lead-local
    schedule = build_schedule([make_lead()], utc_config(), now=now)
    assert len(schedule.calls) == 1
    assert schedule.calls[0].scheduled_at == datetime(2026, 7, 2, 9, 0, tzinfo=UTC)


def test_window_end_hour_is_exclusive():
    # 17:40 -> next slot boundary is 17:45 (legal); 18:00 would not be.
    now = datetime(2026, 7, 2, 17, 40, tzinfo=UTC)
    schedule = build_schedule([make_lead()], utc_config(), now=now)
    assert schedule.calls[0].scheduled_at == datetime(2026, 7, 2, 17, 45, tzinfo=UTC)


def test_after_hours_rolls_to_next_morning():
    now = datetime(2026, 7, 2, 17, 50, tzinfo=UTC)  # 18:00 slot is outside the window
    schedule = build_schedule([make_lead()], utc_config(), now=now)
    assert schedule.calls[0].scheduled_at == datetime(2026, 7, 3, 9, 0, tzinfo=UTC)


def test_window_boundary_minute_exactly():
    config = utc_config()
    lead = make_lead()
    assert not in_calling_window(lead, datetime(2026, 7, 2, 8, 59, tzinfo=UTC), config)
    assert in_calling_window(lead, datetime(2026, 7, 2, 9, 0, tzinfo=UTC), config)
    assert in_calling_window(lead, datetime(2026, 7, 2, 17, 59, tzinfo=UTC), config)
    assert not in_calling_window(lead, datetime(2026, 7, 2, 18, 0, tzinfo=UTC), config)


def test_unknown_timezone_uses_conservative_dual_clock():
    config = CampaignConfig(window_start_hour=9, window_end_hour=18)
    lead = make_lead(timezone="")
    # 14:00 UTC is a fine hour on the US East Coast but pre-9am on the West
    # Coast -> an unmapped number must NOT be dialable yet.
    now = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
    assert not in_calling_window(lead, now, config)

    schedule = build_schedule([lead], config, now=now)
    assert len(schedule.calls) == 1
    slot = schedule.calls[0].scheduled_at
    assert slot > now
    for zone_name in ("America/New_York", "America/Los_Angeles"):
        local_hour = slot.astimezone(resolve_tz(zone_name)).hour
        assert 9 <= local_hour < 18


def test_lead_local_window_not_campaign_window():
    # 15:00 UTC: in-window for a UTC lead; for a Pacific lead it is early morning.
    now = datetime(2026, 7, 2, 15, 0, tzinfo=UTC)
    config = utc_config()
    pacific = make_lead(lead_id="L-PT", timezone="America/Los_Angeles")
    schedule = build_schedule([pacific], config, now=now)
    slot = schedule.calls[0].scheduled_at
    local_hour = slot.astimezone(resolve_tz("America/Los_Angeles")).hour
    assert 9 <= local_hour < 18
    assert slot > now


# ---------------------------------------------------------------------------
# suppression / eligibility
# ---------------------------------------------------------------------------


def test_suppressed_number_is_never_scheduled():
    suppression = SuppressionList(["(555) 010-1"])  # too short -> ignored
    suppression.add("+1 555 0101")
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    schedule = build_schedule([make_lead()], utc_config(), suppression, now=now)
    assert schedule.calls == []
    assert schedule.skipped[0].reason == "suppressed"


def test_terminal_and_in_flight_states_are_skipped():
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    leads = [
        make_lead(lead_id="L-OPT", state=LeadState.OPTED_OUT),
        make_lead(lead_id="L-BOOK", phone="+1-555-0102", state=LeadState.BOOKED),
        make_lead(lead_id="L-FLIGHT", phone="+1-555-0103", state=LeadState.ATTEMPTED),
    ]
    schedule = build_schedule(leads, utc_config(), now=now)
    assert schedule.calls == []
    reasons = {s.lead_id: s.reason for s in schedule.skipped}
    assert reasons["L-OPT"] == "state_not_callable:opted_out"
    assert reasons["L-BOOK"] == "state_not_callable:booked"
    assert reasons["L-FLIGHT"] == "state_not_callable:attempted"


def test_attempt_cap_is_enforced():
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    lead = make_lead(attempts=4)
    schedule = build_schedule([lead], utc_config(max_attempts=4), now=now)
    assert schedule.calls == []
    assert schedule.skipped[0].reason == "attempt_cap_reached"


def test_invalid_phone_is_skipped():
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    schedule = build_schedule([make_lead(phone="n/a")], utc_config(), now=now)
    assert schedule.skipped[0].reason == "invalid_phone"


# ---------------------------------------------------------------------------
# cooldown / caps / determinism
# ---------------------------------------------------------------------------


def test_cooldown_pushes_the_next_attempt_out():
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    cooldown = datetime(2026, 7, 3, 11, 7, tzinfo=UTC)
    lead = make_lead(state=LeadState.VOICEMAIL, attempts=1, cooldown_until=cooldown)
    schedule = build_schedule([lead], utc_config(), now=now)
    call = schedule.calls[0]
    assert call.scheduled_at >= cooldown
    assert call.scheduled_at == datetime(2026, 7, 3, 11, 15, tzinfo=UTC)  # slot-aligned
    assert call.attempt_number == 2


def test_cooldown_beyond_horizon_means_no_capacity():
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    lead = make_lead(
        state=LeadState.VOICEMAIL,
        attempts=1,
        cooldown_until=now + timedelta(days=30),
    )
    schedule = build_schedule([lead], utc_config(horizon_days=3), now=now)
    assert schedule.calls == []
    assert schedule.skipped[0].reason == "no_capacity_in_horizon"


def test_daily_cap_rolls_overflow_to_the_next_day():
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    leads = [
        make_lead(lead_id=f"L-{i}", phone=f"+1-555-010{i}") for i in range(1, 4)
    ]
    schedule = build_schedule(leads, utc_config(daily_cap=2), now=now)
    days = sorted(c.scheduled_at.date().isoformat() for c in schedule.calls)
    assert len(schedule.calls) == 3
    assert days == ["2026-07-02", "2026-07-02", "2026-07-03"]


def test_no_two_calls_share_a_slot():
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    leads = [
        make_lead(lead_id=f"L-{i}", phone=f"+1-555-01{i:02d}") for i in range(1, 9)
    ]
    schedule = build_schedule(leads, utc_config(), now=now)
    slots = [c.scheduled_at for c in schedule.calls]
    assert len(slots) == len(set(slots)) == 8


def test_schedule_is_deterministic():
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    leads = [
        make_lead(lead_id="L-2", phone="+1-555-0102", job_value_band="high"),
        make_lead(lead_id="L-1", phone="+1-555-0101"),
        make_lead(lead_id="L-3", phone="+1-555-0103", timezone=""),
    ]
    first = build_schedule(leads, utc_config(), now=now).to_dict()
    second = build_schedule(leads, utc_config(), now=now).to_dict()
    assert first == second


def test_higher_ranked_lead_gets_the_earlier_slot():
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    low = make_lead(lead_id="L-LOW", phone="+1-555-0101", job_value_band="low")
    high = make_lead(lead_id="L-HIGH", phone="+1-555-0102", job_value_band="high")
    schedule = build_schedule([low, high], utc_config(), now=now)
    by_id = {c.lead_id: c.scheduled_at for c in schedule.calls}
    assert by_id["L-HIGH"] < by_id["L-LOW"]
