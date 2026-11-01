"""Did approved actions move deals? Compare against accounts that matched but got no approved action."""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from revloop.extract import read_crm_rows
from revloop.facts import iso, normalize_stage, stage_rank
from revloop.store import Store

MIN_GROUP_SIZE = 10


@dataclass(frozen=True)
class Group:
    n: int
    advanced: int

    @property
    def rate(self) -> float:
        return self.advanced / self.n if self.n else 0.0


@dataclass(frozen=True)
class Comparison:
    playbook: str
    treated: Group  # at least one action approved
    holdout: Group  # matched the trigger, nothing approved
    window_open: int  # matches too recent to judge yet
    unmeasurable: int  # stage at match time unknown or not an open stage

    @property
    def enough_data(self) -> bool:
        return self.treated.n >= MIN_GROUP_SIZE and self.holdout.n >= MIN_GROUP_SIZE

    @property
    def uplift(self) -> float:
        return self.treated.rate - self.holdout.rate


def import_snapshots(store: Store, path: Path) -> int:
    added = 0
    for row in read_crm_rows(path):
        if row.get("stage"):
            added += store.add_snapshot(
                row["account"], normalize_stage(row["stage"]), datetime.fromisoformat(row["date"])
            )
    return added


def two_proportion_z(treated: Group, holdout: Group) -> tuple[float, float]:
    """Pooled two-proportion z-test. Returns (z, two-sided p-value)."""
    pooled = (treated.advanced + holdout.advanced) / (treated.n + holdout.n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / treated.n + 1 / holdout.n))
    if se == 0:
        return 0.0, 1.0
    z = (treated.rate - holdout.rate) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


def advanced_within(store: Store, account: str, stage: str, start: str, end: str) -> bool:
    start_rank = stage_rank(stage)
    for snap in store.snapshots(account):
        rank = stage_rank(snap["stage"])
        if start < snap["observed_at"] <= end and rank is not None and rank > start_rank:
            return True
    return False


def compare_playbooks(store: Store, as_of: datetime, window_days: int = 30) -> list[Comparison]:
    # One match per (playbook, account); treated if any of its actions was approved.
    matches: dict[tuple[str, str], dict] = {}
    for action in store.actions():
        key = (action["playbook"], action["account"])
        match = matches.setdefault(
            key, {"at": action["created_at"], "stage": action["stage_at_match"], "approved": False}
        )
        match["approved"] |= action["status"] == "approved"

    counts: dict[str, dict] = defaultdict(lambda: {"treated": [0, 0], "holdout": [0, 0], "open": 0, "unmeasurable": 0})
    for (playbook, account), match in matches.items():
        bucket = counts[playbook]
        end = iso(datetime.fromisoformat(match["at"]) + timedelta(days=window_days))
        if stage_rank(match["stage"]) is None:
            bucket["unmeasurable"] += 1
        elif end > iso(as_of):
            bucket["open"] += 1
        else:
            group = bucket["treated" if match["approved"] else "holdout"]
            group[0] += 1
            group[1] += advanced_within(store, account, match["stage"], match["at"], end)

    return [
        Comparison(playbook, Group(*c["treated"]), Group(*c["holdout"]), c["open"], c["unmeasurable"])
        for playbook, c in sorted(counts.items())
    ]


def format_report(comparisons: list[Comparison], window_days: int) -> str:
    if not comparisons:
        return "No playbook matches yet. Run a playbook first."
    lines = [f"Share of matched accounts that advanced stage within {window_days} days of the match", ""]
    for c in comparisons:
        lines.append(f"{c.playbook}")
        lines.append(f"  approved action : {c.treated.advanced}/{c.treated.n} advanced ({c.treated.rate:.0%})")
        lines.append(f"  holdout         : {c.holdout.advanced}/{c.holdout.n} advanced ({c.holdout.rate:.0%})")
        if c.window_open or c.unmeasurable:
            lines.append(
                f"  excluded        : {c.window_open} window still open, {c.unmeasurable} unknown starting stage"
            )
        if c.enough_data:
            z, p = two_proportion_z(c.treated, c.holdout)
            lines.append(f"  uplift          : {c.uplift:+.0%} (two-proportion z-test: z={z:.2f}, p={p:.3f})")
        else:
            lines.append(f"  uplift          : not enough data (need >= {MIN_GROUP_SIZE} accounts per group)")
        lines.append("")
    lines.append("Not a randomised test: a human chose which actions to approve, so selection bias is likely.")
    return "\n".join(lines)
