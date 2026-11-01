"""MCP server so any agent (Claude Code, Cursor, ...) can read and write the shared context."""

from dataclasses import asdict
from datetime import UTC, datetime

from mcp.server.mcpserver import MCPServer

from revloop.outcomes import compare_playbooks
from revloop.store import Store


def get_account_context(store: Store, account: str) -> dict:
    if not store.has_account(account):
        return {"error": f"unknown account: {account}", "known_accounts": store.account_names()}
    return {"account": account, "current": store.latest_by_kind(account), "facts": store.facts(account)}


def search_facts(store: Store, query: str, kind: str | None = None) -> list[dict]:
    return store.search_facts(query, kind)


def propose_action(store: Store, account: str, kind: str, body: str, playbook: str = "agent") -> dict:
    if not store.has_account(account):
        return {"error": f"unknown account: {account}"}
    if not body.strip():
        return {"error": "body must not be empty"}
    action_id = store.add_action(account, playbook, kind, body.strip(), datetime.now(UTC))
    store.commit()
    return {"id": action_id, "status": "proposed", "note": "A human must approve this with `revloop approve`."}


def list_pending_actions(store: Store) -> list[dict]:
    return store.actions("proposed")


def outcome_report(store: Store, window_days: int = 30) -> list[dict]:
    report = []
    for comparison in compare_playbooks(store, datetime.now(UTC), window_days):
        row = asdict(comparison)
        row["enough_data"] = comparison.enough_data
        row["uplift"] = comparison.uplift if comparison.enough_data else None
        report.append(row)
    return report


def build_server(db_path: str) -> MCPServer:
    server = MCPServer("revloop", instructions="Shared GTM context: account facts, proposed actions, outcomes.")

    @server.tool(name="get_account_context")
    def get_account_context_tool(account: str) -> dict:
        """Current facts (stage, amount, pains, objections, competitors, ...) for one account, with sources."""
        with Store(db_path) as store:
            return get_account_context(store, account)

    @server.tool(name="search_facts")
    def search_facts_tool(query: str, kind: str | None = None) -> list[dict]:
        """Search current facts by text across all accounts. Optional kind filter, e.g. 'competitor'."""
        with Store(db_path) as store:
            return search_facts(store, query, kind)

    @server.tool(name="propose_action")
    def propose_action_tool(account: str, kind: str, body: str) -> dict:
        """Propose an action (e.g. kind 'email_draft', 'crm_note', 'task'). Nothing is sent; a human approves."""
        with Store(db_path) as store:
            return propose_action(store, account, kind, body)

    @server.tool(name="list_pending_actions")
    def list_pending_actions_tool() -> list[dict]:
        """Actions waiting for human approval."""
        with Store(db_path) as store:
            return list_pending_actions(store)

    @server.tool(name="outcome_report")
    def outcome_report_tool(window_days: int = 30) -> list[dict]:
        """Per playbook: share of accounts that advanced stage with an approved action vs a holdout."""
        with Store(db_path) as store:
            return outcome_report(store, window_days)

    return server
