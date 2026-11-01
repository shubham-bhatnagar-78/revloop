from datetime import datetime

import pytest
from conftest import PLAYBOOKS

from revloop.facts import Fact
from revloop.playbooks import find_playbook, load_playbook, matches, run_playbook

NOW = datetime(2026, 9, 30)


def add(store, account, kind, value, day):
    store.add_fact(Fact(account, kind, value, "test", datetime(2026, 9, day) if day > 0 else datetime(2026, 8, -day)))


def test_shipped_playbooks_load():
    stalled = find_playbook("stalled-deal", PLAYBOOKS)
    competitor = find_playbook("competitor-mentioned", PLAYBOOKS)
    assert stalled.trigger == {"stage_in": ["proposal", "negotiation"], "no_new_facts_days": 21}
    assert [a["type"] for a in competitor.actions] == ["crm_note", "task"]


def test_stalled_deal_needs_late_stage_and_silence(store):
    stalled = find_playbook("stalled-deal", PLAYBOOKS)
    add(store, "Quiet Co", "stage", "negotiation", -1)  # Aug 1: 60 days quiet
    add(store, "Busy Co", "stage", "negotiation", 25)  # 5 days ago
    add(store, "Early Co", "stage", "discovery", -1)
    assert [a for a in store.account_names() if matches(store, a, stalled.trigger, NOW)] == ["Quiet Co"]


def test_stalled_draft_references_latest_pain_and_objection(store):
    add(store, "Quiet Co", "stage", "proposal", -1)
    add(store, "Quiet Co", "pain", "Old pain.", -1)
    add(store, "Quiet Co", "pain", "Newest pain.", -5)
    add(store, "Quiet Co", "objection", "Too expensive.", -2)
    add(store, "Quiet Co", "stakeholder", "Lena Okafor", -2)

    [action_id] = run_playbook(store, find_playbook("stalled-deal", PLAYBOOKS), NOW)
    [action] = store.actions()
    assert action["id"] == action_id and action["status"] == "proposed" and action["stage_at_match"] == "proposal"
    assert '"Newest pain."' in action["body"] and '"Too expensive."' in action["body"]
    assert "Hi Lena Okafor" in action["body"]


def test_competitor_playbook_proposes_note_and_task_once(store):
    add(store, "Northwind Robotics", "competitor", "PipeIQ", 1)
    add(store, "Other Co", "pain", "slow", 1)
    playbook = find_playbook("competitor-mentioned", PLAYBOOKS)

    assert len(run_playbook(store, playbook, NOW)) == 2
    assert run_playbook(store, playbook, NOW) == []  # already handled: no duplicate proposals
    assert {a["kind"] for a in store.actions()} == {"crm_note", "task"}
    assert "Review the PipeIQ battlecard" in store.actions()[1]["body"]


def test_approve_and_reject_flow(store):
    add(store, "Northwind Robotics", "competitor", "PipeIQ", 1)
    note_id, task_id = run_playbook(store, find_playbook("competitor-mentioned", PLAYBOOKS), NOW)

    assert store.decide(note_id, "approved", NOW)
    assert store.decide(task_id, "rejected", NOW)
    assert not store.decide(note_id, "rejected", NOW)  # decisions are final
    assert not store.decide(999, "approved", NOW)
    assert store.actions("proposed") == []
    assert [a["id"] for a in store.actions("approved")] == [note_id]


def test_unknown_trigger_key_is_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("name: bad\ntrigger:\n  stage_is: proposal\nactions: []\n")
    with pytest.raises(ValueError, match="stage_is"):
        load_playbook(path)
