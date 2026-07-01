"""Ranking: explainable win-back scoring."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from dbreactivation import Lead, rank_leads, score_lead
from dbreactivation.ranking import (
    engagement_factor,
    recency_factor,
    seasonality_factor,
    value_factor,
)

NOW = datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def make_lead(**overrides) -> Lead:
    base = dict(
        lead_id="L-1",
        phone="+1-555-0101",
        name="Test Lead",
        timezone="America/New_York",
        last_contact=TODAY - timedelta(days=60),
        prior_jobs=0,
        responded_before=False,
        inbound_inquiry=False,
        job_value_band="mid",
        service="hvac",
    )
    base.update(overrides)
    return Lead(**base)


def test_sweet_spot_beats_too_fresh():
    dormant = make_lead(lead_id="L-A", last_contact=TODAY - timedelta(days=60))
    fresh = make_lead(lead_id="L-B", last_contact=TODAY - timedelta(days=10))
    ranked = rank_leads([fresh, dormant], now=NOW)
    assert [r.lead.lead_id for r in ranked] == ["L-A", "L-B"]


def test_stale_lead_hits_recency_floor():
    factor = recency_factor(make_lead(last_contact=TODAY - timedelta(days=900)), TODAY)
    assert factor.value == 0.1
    assert "stale" in factor.detail


def test_unknown_last_contact_gets_neutral_recency():
    factor = recency_factor(make_lead(last_contact=None), TODAY)
    assert factor.value == 0.4


def test_value_band_ordering():
    high = value_factor(make_lead(job_value_band="high")).value
    mid = value_factor(make_lead(job_value_band="mid")).value
    low = value_factor(make_lead(job_value_band="low")).value
    unknown = value_factor(make_lead(job_value_band="")).value
    assert high > mid > unknown > low or high > mid > low
    assert high == 1.0 and low == 0.3


def test_engagement_signals_stack_and_cap():
    quiet = engagement_factor(make_lead())
    engaged = engagement_factor(
        make_lead(prior_jobs=2, responded_before=True, inbound_inquiry=True)
    )
    maxed = engagement_factor(
        make_lead(prior_jobs=50, responded_before=True, inbound_inquiry=True)
    )
    assert quiet.value < engaged.value <= maxed.value
    assert maxed.value == 1.0  # capped even with absurd prior_jobs


def test_seasonality_peak_vs_off_season():
    july = date(2026, 7, 15)
    hvac = seasonality_factor(make_lead(service="hvac"), july)
    roofing = seasonality_factor(make_lead(service="roofing"), july)
    unknown = seasonality_factor(make_lead(service="notary"), july)
    assert hvac.value == 1.0  # July is HVAC season
    assert roofing.value == 0.5  # July is roofing off-season
    assert unknown.value == 0.7  # no profile -> neutral


def test_score_is_bounded_0_to_100():
    best = score_lead(
        make_lead(
            prior_jobs=3,
            responded_before=True,
            inbound_inquiry=True,
            job_value_band="high",
            last_contact=TODAY - timedelta(days=45),
        ),
        now=NOW,
    )
    worst = score_lead(
        make_lead(
            job_value_band="low",
            service="roofing",
            last_contact=TODAY - timedelta(days=1000),
        ),
        now=NOW,
    )
    assert 0 <= worst.score < best.score <= 100


def test_factor_breakdown_explains_the_score():
    ranked = score_lead(make_lead(), now=NOW)
    assert {f.name for f in ranked.factors} == {
        "recency",
        "engagement",
        "value",
        "seasonality",
    }
    reconstructed = round(sum(f.weighted for f in ranked.factors) * 100, 1)
    assert reconstructed == ranked.score
    assert all(f.detail for f in ranked.factors)  # every factor says *why*


def test_ties_break_deterministically_by_lead_id():
    a = make_lead(lead_id="L-B")
    b = make_lead(lead_id="L-A")
    ranked = rank_leads([a, b], now=NOW)
    assert ranked[0].score == ranked[1].score
    assert [r.lead.lead_id for r in ranked] == ["L-A", "L-B"]
