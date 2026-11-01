"""Turn GTM artifacts (call transcripts, emails, CRM exports) into timestamped facts."""

import csv
import email
import email.policy
import email.utils
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from revloop.facts import Fact, normalize_stage

SPEAKER_LINE = re.compile(r"^([A-Z][\w.'-]*(?: [A-Z][\w.'-]*)*):\s+(.+)$")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

KEYWORDS = {
    "pain": ("struggl", "pain", "frustrat", "manual", "too slow", "takes us", "losing", "bottleneck", "waste"),
    "objection": ("expensive", "pricing", "budget", "concern", "worried", "not sure", "security review", "hesitant"),
    "next_step": ("next step", "let's schedule", "follow up", "follow-up", "send over", "set up a call", "book a"),
}
COMPETITOR = re.compile(
    r"\b(?:evaluating|trialing|looking at|compared (?:to|with)|switch(?:ing|ed)? from|quote from|demo from)\s+"
    r"([A-Z][A-Za-z0-9]+(?: [A-Z][A-Za-z0-9]+)?)"
)

# CRM export column names: HubSpot first, then Salesforce, then generic. Matched case-insensitively.
CSV_ALIASES = {
    "account": ("company name", "account name", "company", "account"),
    "stage": ("deal stage", "stage"),
    "amount": ("amount",),
    "next_step": ("next step",),
    "date": ("last modified date", "last activity date", "snapshot date", "date"),
}


@dataclass(frozen=True)
class Line:
    number: int
    speaker: str | None
    text: str


@dataclass(frozen=True)
class Document:
    account: str
    source: str
    observed_at: datetime
    lines: list[Line]
    rep: str | None = None  # our own seller; their lines are not customer signal

    def text(self) -> str:
        return "\n".join(f"{line.speaker}: {line.text}" if line.speaker else line.text for line in self.lines)


class Extractor(Protocol):
    def extract(self, doc: Document) -> list[Fact]: ...


class RuleExtractor:
    """Deterministic keyword and pattern rules. Offline, fast, and predictable, but shallow."""

    def extract(self, doc: Document) -> list[Fact]:
        facts = []
        speakers_seen = set()

        def add(kind: str, value: str, line: Line) -> None:
            facts.append(Fact(doc.account, kind, value, f"{doc.source}:{line.number}", doc.observed_at))

        for line in doc.lines:
            from_customer = line.speaker != doc.rep
            if line.speaker and from_customer and line.speaker not in speakers_seen:
                speakers_seen.add(line.speaker)
                add("stakeholder", line.speaker, line)
            for sentence in SENTENCE_END.split(line.text):
                lowered = sentence.lower()
                for kind, words in KEYWORDS.items():
                    if (from_customer or kind == "next_step") and any(word in lowered for word in words):
                        add(kind, sentence.strip(), line)
                if from_customer:
                    for name in COMPETITOR.findall(sentence):
                        add("competitor", name, line)
        return facts


def parse_transcript(path: Path) -> Document:
    """Header block (Account:, Date:, Rep:), a blank line, then 'Speaker: line' turns."""
    raw = path.read_text(encoding="utf-8").splitlines()
    headers: dict[str, str] = {}
    lines = []
    in_header = True
    for number, text in enumerate(raw, start=1):
        if in_header:
            if not text.strip():
                in_header = False
            elif ":" in text:
                key, value = text.split(":", 1)
                headers[key.strip().lower()] = value.strip()
            continue
        match = SPEAKER_LINE.match(text.strip())
        if match:
            lines.append(Line(number, match.group(1), match.group(2)))
    if "account" not in headers or "date" not in headers:
        raise ValueError(f"{path}: transcript needs 'Account:' and 'Date:' header lines")
    return Document(headers["account"], str(path), datetime.fromisoformat(headers["date"]), lines, headers.get("rep"))


def parse_email(path: Path) -> Document:
    """The sender is a stakeholder at the account named in the Organization header."""
    message = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    sender_name, sender_address = email.utils.parseaddr(message["From"])
    account = message["Organization"] or sender_address.split("@")[-1]
    body = message.get_body(preferencelist=("plain",))
    text = body.get_content() if body else ""
    lines = [Line(n, sender_name or sender_address, t.strip()) for n, t in enumerate(text.splitlines(), 1) if t.strip()]
    return Document(str(account), str(path), email.utils.parsedate_to_datetime(message["Date"]), lines)


def read_crm_rows(path: Path) -> list[dict[str, str]]:
    """Read a CRM CSV export and map aliased column names onto canonical field names."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = {name.strip().lower(): name for name in reader.fieldnames or []}
        columns = {}
        for field, aliases in CSV_ALIASES.items():
            column = next((headers[alias] for alias in aliases if alias in headers), None)
            if column:
                columns[field] = column
        for required in ("account", "date"):
            if required not in columns:
                raise ValueError(f"{path}: no column for '{required}' (tried {', '.join(CSV_ALIASES[required])})")
        return [{field: row[column].strip() for field, column in columns.items()} for row in reader]


def crm_facts(path: Path) -> list[Fact]:
    facts = []
    for number, row in enumerate(read_crm_rows(path), start=2):
        observed = datetime.fromisoformat(row["date"])
        source = f"{path}:{number}"
        if row.get("stage"):
            facts.append(Fact(row["account"], "stage", normalize_stage(row["stage"]), source, observed))
        if row.get("amount"):
            facts.append(
                Fact(row["account"], "amount", row["amount"].replace("$", "").replace(",", ""), source, observed)
            )
        if row.get("next_step"):
            facts.append(Fact(row["account"], "next_step", row["next_step"], source, observed))
    return facts


def facts_from_file(path: Path, extractor: Extractor) -> list[Fact]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return crm_facts(path)
    if suffix == ".txt":
        return extractor.extract(parse_transcript(path))
    if suffix == ".eml":
        return extractor.extract(parse_email(path))
    return []
