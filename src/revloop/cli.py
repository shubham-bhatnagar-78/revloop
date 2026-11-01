"""revloop command line."""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from revloop.extract import facts_from_file
from revloop.llm import choose_extractor
from revloop.outcomes import compare_playbooks, format_report, import_snapshots
from revloop.playbooks import find_playbook, run_playbook
from revloop.store import Store

INGESTABLE = {".txt", ".eml", ".csv"}


def moment(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def cmd_init(store: Store, args) -> None:
    print(f"initialised {args.db}")


def cmd_ingest(store: Store, args) -> None:
    path = Path(args.path)
    files = sorted(p for p in path.rglob("*") if p.suffix.lower() in INGESTABLE) if path.is_dir() else [path]
    extractor = choose_extractor(args.extractor)
    print(f"extractor: {type(extractor).__name__}")
    for file in files:
        facts = facts_from_file(file, extractor)
        added = sum(store.add_fact(fact) for fact in facts)
        print(f"  {file}: {len(facts)} facts extracted, {added} new")


def cmd_context(store: Store, args) -> None:
    if not store.has_account(args.account):
        sys.exit(f"unknown account: {args.account}")
    print(args.account)
    for fact in store.facts(args.account, include_superseded=args.history):
        marker = "  (superseded)" if fact["superseded_by"] else ""
        print(f"  {fact['kind']:<12} {fact['value']}{marker}")
        print(f"  {'':<12} from {fact['source']} at {fact['observed_at'][:10]}")


def cmd_run(store: Store, args) -> None:
    playbook = find_playbook(args.playbook, Path(args.playbooks))
    ids = run_playbook(store, playbook, args.now)
    print(f"{playbook.name}: proposed {len(ids)} actions" + (f" (ids {', '.join(map(str, ids))})" if ids else ""))


def cmd_actions(store: Store, args) -> None:
    for action in store.actions(args.status):
        print(f"#{action['id']} [{action['status']}] {action['playbook']} / {action['kind']} for {action['account']}")
        print("    " + action["body"].replace("\n", "\n    "))


def decide(status: str):
    def command(store: Store, args) -> None:
        if not store.decide(args.id, status, datetime.now(UTC)):
            sys.exit(f"action #{args.id} not found or already decided")
        print(f"action #{args.id} {status}")

    return command


def cmd_outcomes_import(store: Store, args) -> None:
    print(f"imported {import_snapshots(store, Path(args.csv))} new stage snapshots")


def cmd_report(store: Store, args) -> None:
    print(format_report(compare_playbooks(store, args.now, args.window), args.window))


def cmd_mcp(store: Store, args) -> None:
    from revloop.server import build_server

    build_server(args.db).run("stdio")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="revloop", description="Shared, current GTM context for teams and agents.")
    parser.add_argument("--db", default="revloop.db", help="SQLite database path (default: ./revloop.db)")
    sub = parser.add_subparsers(dest="command", required=True)
    now = {"type": moment, "default": datetime.now(UTC), "help": "evaluate as of this date (YYYY-MM-DD)"}

    sub.add_parser("init", help="create the database").set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="extract facts from a file or directory")
    p.add_argument("path")
    p.add_argument("--extractor", choices=["auto", "rules", "llm"], default="auto")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("context", help="show current facts for an account")
    p.add_argument("account")
    p.add_argument("--history", action="store_true", help="include superseded facts")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("run", help="run a playbook and propose actions")
    p.add_argument("playbook", help="playbook name or path to a .yaml file")
    p.add_argument("--playbooks", default="playbooks", help="playbook directory (default: ./playbooks)")
    p.add_argument("--now", **now)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("actions", help="list actions")
    p.add_argument("--status", choices=["proposed", "approved", "rejected"])
    p.set_defaults(func=cmd_actions)

    for name, status in (("approve", "approved"), ("reject", "rejected")):
        p = sub.add_parser(name, help=f"{name} a proposed action")
        p.add_argument("id", type=int)
        p.set_defaults(func=decide(status))

    outcomes = sub.add_parser("outcomes", help="stage snapshots for outcome measurement")
    outcomes_sub = outcomes.add_subparsers(dest="outcomes_command", required=True)
    p = outcomes_sub.add_parser("import", help="import a CRM stage snapshot CSV")
    p.add_argument("csv")
    p.set_defaults(func=cmd_outcomes_import)

    p = sub.add_parser("report", help="did approved actions move deals?")
    p.add_argument("--window", type=int, default=30, help="days after a match to look for a stage advance")
    p.add_argument("--now", **now)
    p.set_defaults(func=cmd_report)

    sub.add_parser("mcp", help="start the MCP server on stdio").set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    with Store(args.db) as store:
        args.func(store, args)
