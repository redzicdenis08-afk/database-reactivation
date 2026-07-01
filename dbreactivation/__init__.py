"""dbreactivation — win-back engine for dormant leads.

Reference implementation of a database-reactivation pipeline: rank a
business's dead leads by win-back potential, schedule compliant call
attempts, enforce the lead lifecycle, and feed call outcomes back into
the loop. Pure standard library, deterministic, compliance-first.
"""
from .intake import (
    IngestReport,
    OutcomeRecord,
    classify_transcript,
    ingest_outcomes,
)
from .lifecycle import (
    TRANSITIONS,
    LifecycleError,
    can_transition,
    is_terminal,
    transition,
)
from .models import (
    CallOutcome,
    CampaignConfig,
    Lead,
    LeadState,
    SuppressionList,
    normalize_phone,
)
from .ranking import Factor, RankedLead, rank_leads, score_lead
from .readiness import CheckResult, ReadinessReport, run_readiness
from .scheduler import Schedule, ScheduledCall, build_schedule, in_calling_window

__version__ = "0.1.0"

__all__ = [
    "CallOutcome",
    "CampaignConfig",
    "CheckResult",
    "Factor",
    "IngestReport",
    "Lead",
    "LeadState",
    "LifecycleError",
    "OutcomeRecord",
    "RankedLead",
    "ReadinessReport",
    "Schedule",
    "ScheduledCall",
    "SuppressionList",
    "TRANSITIONS",
    "build_schedule",
    "can_transition",
    "classify_transcript",
    "in_calling_window",
    "ingest_outcomes",
    "is_terminal",
    "normalize_phone",
    "rank_leads",
    "run_readiness",
    "score_lead",
    "transition",
    "__version__",
]
