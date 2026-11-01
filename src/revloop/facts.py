"""Fact model, fact kinds, and deal stage normalisation."""

from dataclasses import dataclass
from datetime import UTC, datetime

SINGLE_VALUED = frozenset({"stage", "amount", "next_step"})
MULTI_VALUED = frozenset({"pain", "objection", "competitor", "stakeholder"})
KINDS = SINGLE_VALUED | MULTI_VALUED

# Open pipeline stages in order. closed_lost is deliberately absent: it has no rank,
# so moving to it never counts as advancing.
STAGE_ORDER = ["prospecting", "qualification", "discovery", "demo", "proposal", "negotiation", "closed_won"]

# HubSpot internal ids/labels and Salesforce default picklist values.
STAGE_ALIASES = {
    "appointmentscheduled": "discovery",
    "appointment scheduled": "discovery",
    "qualifiedtobuy": "qualification",
    "qualified to buy": "qualification",
    "presentationscheduled": "demo",
    "presentation scheduled": "demo",
    "decisionmakerboughtin": "proposal",
    "decision maker bought-in": "proposal",
    "contractsent": "negotiation",
    "contract sent": "negotiation",
    "closedwon": "closed_won",
    "closed won": "closed_won",
    "closedlost": "closed_lost",
    "closed lost": "closed_lost",
    "needs analysis": "discovery",
    "id. decision makers": "qualification",
    "value proposition": "demo",
    "proposal/price quote": "proposal",
    "negotiation/review": "negotiation",
}


@dataclass(frozen=True)
class Fact:
    account: str
    kind: str
    value: str
    source: str
    observed_at: datetime


def normalize_stage(raw: str) -> str:
    key = raw.strip().lower()
    return STAGE_ALIASES.get(key, key.replace(" ", "_"))


def stage_rank(stage: str | None) -> int | None:
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else None


def to_utc(moment: datetime) -> datetime:
    """Treat naive datetimes as UTC so every stored timestamp compares correctly as text."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def iso(moment: datetime) -> str:
    return to_utc(moment).isoformat(timespec="seconds")
