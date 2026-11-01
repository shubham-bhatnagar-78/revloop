"""YAML playbooks: trigger conditions over the context store plus action templates filled from facts."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from revloop.facts import iso
from revloop.store import Store

TRIGGER_KEYS = {"stage_in", "no_new_facts_days", "has_kind"}
MISSING = "(none recorded)"


@dataclass(frozen=True)
class Playbook:
    name: str
    description: str
    trigger: dict
    actions: list[dict]


class _Context(dict):
    def __missing__(self, key: str) -> str:
        return MISSING


def load_playbook(path: Path) -> Playbook:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    unknown = set(data.get("trigger", {})) - TRIGGER_KEYS
    if unknown:
        raise ValueError(f"{path}: unknown trigger keys {sorted(unknown)}; allowed: {sorted(TRIGGER_KEYS)}")
    for action in data.get("actions", []):
        if not {"type", "template"} <= set(action):
            raise ValueError(f"{path}: every action needs 'type' and 'template'")
    return Playbook(data["name"], data.get("description", ""), data.get("trigger", {}), data.get("actions", []))


def find_playbook(name_or_path: str, directory: Path) -> Playbook:
    path = Path(name_or_path)
    if path.suffix not in (".yaml", ".yml"):
        path = directory / f"{name_or_path}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"playbook not found: {path}")
    return load_playbook(path)


def matches(store: Store, account: str, trigger: dict, now: datetime) -> bool:
    latest = store.latest_by_kind(account)
    if "stage_in" in trigger and latest.get("stage") not in trigger["stage_in"]:
        return False
    if "has_kind" in trigger and not all(kind in latest for kind in trigger["has_kind"]):
        return False
    if "no_new_facts_days" in trigger:
        last = store.last_observed(account)
        if last is None or last > iso(now - timedelta(days=trigger["no_new_facts_days"])):
            return False
    return True


def run_playbook(store: Store, playbook: Playbook, now: datetime) -> list[int]:
    """Propose this playbook's actions for every matching account not already handled by it."""
    proposed = []
    for account in store.account_names():
        if store.has_actions(account, playbook.name) or not matches(store, account, playbook.trigger, now):
            continue
        context = _Context(store.latest_by_kind(account), account=account)
        for action in playbook.actions:
            body = action["template"].format_map(context).strip()
            proposed.append(store.add_action(account, playbook.name, action["type"], body, now))
    return proposed
