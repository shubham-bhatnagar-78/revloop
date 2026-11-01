from datetime import datetime

from revloop.facts import Fact


def fact(kind, value, day, account="Acme Freight"):
    return Fact(account, kind, value, "test", datetime(2026, 9, day))


def test_newer_single_valued_fact_supersedes_older(store):
    assert store.add_fact(fact("stage", "proposal", 1))
    assert store.add_fact(fact("stage", "negotiation", 5))

    assert store.latest_by_kind("Acme Freight")["stage"] == "negotiation"
    history = store.facts("Acme Freight", include_superseded=True)
    old = next(f for f in history if f["value"] == "proposal")
    new = next(f for f in history if f["value"] == "negotiation")
    assert old["superseded_by"] == new["id"] and new["superseded_by"] is None


def test_late_arriving_older_fact_is_kept_as_history_only(store):
    store.add_fact(fact("amount", "50000", 10))
    assert store.add_fact(fact("amount", "40000", 2))
    assert store.latest_by_kind("Acme Freight")["amount"] == "50000"
    assert len(store.facts("Acme Freight", include_superseded=True)) == 2


def test_repeating_the_current_value_adds_nothing(store):
    store.add_fact(fact("stage", "proposal", 1))
    assert not store.add_fact(fact("stage", "proposal", 9))
    assert len(store.facts("Acme Freight", include_superseded=True)) == 1


def test_multi_valued_facts_accumulate_and_dedupe(store):
    assert store.add_fact(fact("competitor", "PipeIQ", 1))
    assert store.add_fact(fact("competitor", "Dealstream", 2))
    assert not store.add_fact(fact("competitor", "pipeiq", 3))
    values = sorted(f["value"] for f in store.facts("Acme Freight"))
    assert values == ["Dealstream", "PipeIQ"]


def test_accounts_match_case_insensitively(store):
    store.add_fact(fact("pain", "slow", 1, account="Acme Freight"))
    store.add_fact(fact("pain", "manual", 1, account="acme freight"))
    assert store.account_names() == ["Acme Freight"]


def test_search_facts_by_text_and_kind(store):
    store.add_fact(fact("competitor", "PipeIQ", 1))
    store.add_fact(fact("pain", "PipeIQ was too slow", 1, account="Harbor Health"))
    assert {f["account"] for f in store.search_facts("pipeiq")} == {"Acme Freight", "Harbor Health"}
    assert [f["account"] for f in store.search_facts("pipeiq", kind="competitor")] == ["Acme Freight"]
