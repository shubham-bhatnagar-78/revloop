import asyncio
from datetime import datetime

from revloop.facts import Fact
from revloop.server import (
    build_server,
    get_account_context,
    list_pending_actions,
    outcome_report,
    propose_action,
    search_facts,
)
from revloop.store import Store


def seed(store):
    store.add_fact(Fact("Acme Freight", "competitor", "PipeIQ", "call.txt:3", datetime(2026, 9, 1)))
    store.add_fact(Fact("Acme Freight", "stage", "proposal", "crm.csv:2", datetime(2026, 9, 2)))


def test_get_account_context(store):
    seed(store)
    context = get_account_context(store, "Acme Freight")
    assert context["current"] == {"competitor": "PipeIQ", "stage": "proposal"}
    assert {f["source"] for f in context["facts"]} == {"call.txt:3", "crm.csv:2"}
    assert get_account_context(store, "Nobody")["known_accounts"] == ["Acme Freight"]


def test_search_facts(store):
    seed(store)
    assert [f["value"] for f in search_facts(store, "pipe", kind="competitor")] == ["PipeIQ"]


def test_propose_then_list_pending(store):
    seed(store)
    result = propose_action(store, "Acme Freight", "task", "Send the PipeIQ comparison")
    assert result["status"] == "proposed"
    [pending] = list_pending_actions(store)
    assert (pending["id"], pending["playbook"], pending["stage_at_match"]) == (result["id"], "agent", "proposal")
    assert "error" in propose_action(store, "Nobody", "task", "x")
    assert "error" in propose_action(store, "Acme Freight", "task", "  ")


def test_outcome_report_hides_uplift_on_small_samples(store):
    seed(store)
    propose_action(store, "Acme Freight", "task", "x")
    [row] = outcome_report(store)
    assert row["playbook"] == "agent" and row["enough_data"] is False and row["uplift"] is None


def test_server_registers_the_five_tools_and_calls_through(tmp_path):
    db = tmp_path / "t.db"
    with Store(db) as store:
        seed(store)
    server = build_server(str(db))
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names == {"get_account_context", "search_facts", "propose_action", "list_pending_actions", "outcome_report"}
    result = asyncio.run(server.call_tool("search_facts", {"query": "PipeIQ"}))
    assert not result.is_error and "PipeIQ" in str(result.content)
