"""SQLite context store: accounts, facts with supersede history, proposed actions, stage snapshots."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Self

from revloop.facts import MULTI_VALUED, Fact, iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    superseded_by INTEGER REFERENCES facts(id)
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    playbook TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    stage_at_match TEXT,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected')),
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    stage TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE (account_id, stage, observed_at)
);
"""

FACT_COLUMNS = "f.id, a.name AS account, f.kind, f.value, f.source, f.observed_at, f.superseded_by"
ACTION_COLUMNS = "x.id, a.name AS account, x.playbook, x.kind, x.body, x.stage_at_match, x.status, x.created_at"


class Store:
    def __init__(self, path: str | Path):
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self._db.commit()
        self._db.close()

    def _account_id(self, name: str) -> int:
        self._db.execute("INSERT OR IGNORE INTO accounts (name) VALUES (?)", (name.strip(),))
        return self._db.execute("SELECT id FROM accounts WHERE name = ?", (name.strip(),)).fetchone()["id"]

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(row) for row in self._db.execute(sql, params)]

    def account_names(self) -> list[str]:
        return [row["name"] for row in self._db.execute("SELECT name FROM accounts ORDER BY name")]

    def has_account(self, name: str) -> bool:
        return self._db.execute("SELECT 1 FROM accounts WHERE name = ?", (name.strip(),)).fetchone() is not None

    def add_fact(self, fact: Fact) -> bool:
        """Store a fact. Returns False when it adds nothing new.

        Multi-valued kinds accumulate, deduped case-insensitively. For single-valued kinds the
        newest observation is current and older ones point at it via superseded_by.
        """
        account_id = self._account_id(fact.account)
        if fact.kind in MULTI_VALUED:
            duplicate = self._db.execute(
                "SELECT 1 FROM facts WHERE account_id = ? AND kind = ? AND lower(value) = lower(?)",
                (account_id, fact.kind, fact.value),
            ).fetchone()
            if duplicate:
                return False
            self._insert_fact(account_id, fact)
            return True

        current = self._db.execute(
            "SELECT id, value, observed_at FROM facts WHERE account_id = ? AND kind = ? AND superseded_by IS NULL",
            (account_id, fact.kind),
        ).fetchone()
        if current and current["value"] == fact.value:
            return False
        new_id = self._insert_fact(account_id, fact)
        if current and iso(fact.observed_at) >= current["observed_at"]:
            self._db.execute("UPDATE facts SET superseded_by = ? WHERE id = ?", (new_id, current["id"]))
        elif current:  # an older observation arrived late: keep it as history only
            self._db.execute("UPDATE facts SET superseded_by = ? WHERE id = ?", (current["id"], new_id))
        return True

    def _insert_fact(self, account_id: int, fact: Fact) -> int:
        cursor = self._db.execute(
            "INSERT INTO facts (account_id, kind, value, source, observed_at) VALUES (?, ?, ?, ?, ?)",
            (account_id, fact.kind, fact.value, fact.source, iso(fact.observed_at)),
        )
        return cursor.lastrowid

    def facts(self, account: str, include_superseded: bool = False) -> list[dict]:
        active = "" if include_superseded else "AND f.superseded_by IS NULL"
        return self._rows(
            f"SELECT {FACT_COLUMNS} FROM facts f JOIN accounts a ON a.id = f.account_id "
            f"WHERE a.name = ? {active} ORDER BY f.kind, f.observed_at DESC, f.id DESC",
            (account.strip(),),
        )

    def latest_by_kind(self, account: str) -> dict[str, str]:
        """The most recent current value of each kind (facts() is already newest-first)."""
        latest: dict[str, str] = {}
        for fact in self.facts(account):
            latest.setdefault(fact["kind"], fact["value"])
        return latest

    def last_observed(self, account: str) -> str | None:
        row = self._db.execute(
            "SELECT max(f.observed_at) AS last FROM facts f JOIN accounts a ON a.id = f.account_id WHERE a.name = ?",
            (account.strip(),),
        ).fetchone()
        return row["last"]

    def search_facts(self, query: str, kind: str | None = None) -> list[dict]:
        sql = f"SELECT {FACT_COLUMNS} FROM facts f JOIN accounts a ON a.id = f.account_id WHERE f.superseded_by IS NULL"
        sql += " AND (f.value LIKE ? OR a.name LIKE ?)"
        params: tuple = (f"%{query}%", f"%{query}%")
        if kind:
            sql += " AND f.kind = ?"
            params += (kind,)
        return self._rows(sql + " ORDER BY f.observed_at DESC", params)

    def add_action(self, account: str, playbook: str, kind: str, body: str, created_at: datetime) -> int:
        stage = self.latest_by_kind(account).get("stage")
        cursor = self._db.execute(
            "INSERT INTO actions (account_id, playbook, kind, body, stage_at_match, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self._account_id(account), playbook, kind, body, stage, iso(created_at)),
        )
        return cursor.lastrowid

    def has_actions(self, account: str, playbook: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM actions x JOIN accounts a ON a.id = x.account_id WHERE a.name = ? AND x.playbook = ?",
            (account.strip(), playbook),
        ).fetchone()
        return row is not None

    def actions(self, status: str | None = None) -> list[dict]:
        where = "WHERE x.status = ?" if status else ""
        params = (status,) if status else ()
        return self._rows(
            f"SELECT {ACTION_COLUMNS} FROM actions x JOIN accounts a ON a.id = x.account_id {where} ORDER BY x.id",
            params,
        )

    def decide(self, action_id: int, status: str, decided_at: datetime) -> bool:
        """Approve or reject a proposed action. Decided actions cannot change again."""
        cursor = self._db.execute(
            "UPDATE actions SET status = ?, decided_at = ? WHERE id = ? AND status = 'proposed'",
            (status, iso(decided_at), action_id),
        )
        return cursor.rowcount == 1

    def add_snapshot(self, account: str, stage: str, observed_at: datetime) -> bool:
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO outcomes (account_id, stage, observed_at) VALUES (?, ?, ?)",
            (self._account_id(account), stage, iso(observed_at)),
        )
        return cursor.rowcount == 1

    def snapshots(self, account: str) -> list[dict]:
        return self._rows(
            "SELECT o.stage, o.observed_at FROM outcomes o JOIN accounts a ON a.id = o.account_id "
            "WHERE a.name = ? ORDER BY o.observed_at",
            (account.strip(),),
        )

    def commit(self) -> None:
        self._db.commit()
