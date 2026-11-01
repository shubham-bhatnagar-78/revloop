from datetime import datetime

import pytest

from revloop.facts import Fact
from revloop.outcomes import Group, compare_playbooks, format_report, import_snapshots, two_proportion_z

MATCHED = datetime(2026, 9, 1)
AS_OF = datetime(2026, 11, 1)


def seed(store, account, approved, advanced, stage="proposal", playbook="stalled-deal"):
    store.add_fact(Fact(account, "stage", stage, "test", datetime(2026, 8, 1)))
    action_id = store.add_action(account, playbook, "email_draft", "hi", MATCHED)
    if approved:
        store.decide(action_id, "approved", MATCHED)
    store.add_snapshot(account, "negotiation" if advanced else stage, datetime(2026, 9, 20))


def test_two_proportion_z_matches_hand_calculation():
    # 8/12 vs 3/12: pooled p = 11/24, se = sqrt(p(1-p)/6) = 0.2034, z = (0.6667-0.25)/0.2034
    z, p = two_proportion_z(Group(12, 8), Group(12, 3))
    assert z == pytest.approx(2.0485, abs=1e-3)
    assert p == pytest.approx(0.0405, abs=1e-3)


def test_identical_rates_give_no_signal():
    assert two_proportion_z(Group(10, 0), Group(10, 0)) == (0.0, 1.0)


def test_uplift_compares_approved_against_holdout(store):
    for i in range(12):
        seed(store, f"T{i}", approved=True, advanced=i < 8)
        seed(store, f"H{i}", approved=False, advanced=i < 3)
    [c] = compare_playbooks(store, AS_OF)

    assert (c.treated.n, c.treated.advanced, c.holdout.n, c.holdout.advanced) == (12, 8, 12, 3)
    assert c.enough_data and c.uplift == pytest.approx(5 / 12)
    report = format_report([c], 30)
    assert "+42%" in report and "z=2.05" in report and "not enough data" not in report


def test_small_samples_report_not_enough_data(store):
    for i in range(9):
        seed(store, f"T{i}", approved=True, advanced=True)
        seed(store, f"H{i}", approved=False, advanced=False)
    [c] = compare_playbooks(store, AS_OF)
    assert not c.enough_data
    assert "not enough data (need >= 10 accounts per group)" in format_report([c], 30)


def test_advance_outside_window_or_to_lost_does_not_count(store):
    seed(store, "Late", approved=True, advanced=False)
    store.add_snapshot("Late", "negotiation", datetime(2026, 10, 15))  # 44 days after match
    seed(store, "Lost", approved=True, advanced=False)
    store.add_snapshot("Lost", "closed_lost", datetime(2026, 9, 10))
    [c] = compare_playbooks(store, AS_OF, window_days=30)
    assert (c.treated.n, c.treated.advanced) == (2, 0)


def test_open_windows_and_unknown_stages_are_excluded(store):
    seed(store, "Recent", approved=True, advanced=True)
    seed(store, "Odd", approved=True, advanced=True, stage="custom_stage")
    [c] = compare_playbooks(store, datetime(2026, 9, 15))
    assert (c.treated.n, c.window_open, c.unmeasurable) == (0, 1, 1)


def test_import_hubspot_snapshots_is_idempotent(store, tmp_path):
    path = tmp_path / "snap.csv"
    path.write_text("Company name,Deal Stage,Snapshot Date\nAcme Freight,closedwon,2026-10-20\n")
    assert import_snapshots(store, path) == 1
    assert import_snapshots(store, path) == 0
    assert store.snapshots("Acme Freight")[0]["stage"] == "closed_won"
