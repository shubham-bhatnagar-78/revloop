import pytest
from conftest import EXAMPLES

from revloop.extract import RuleExtractor, crm_facts, parse_email, parse_transcript, read_crm_rows

CALLS = EXAMPLES / "artifacts" / "calls"
EMAILS = EXAMPLES / "artifacts" / "emails"


def by_kind(facts):
    grouped = {}
    for fact in facts:
        grouped.setdefault(fact.kind, []).append(fact.value)
    return grouped


def test_transcript_rules_extract_customer_signal_with_provenance():
    doc = parse_transcript(CALLS / "2026-09-08-northwind-robotics.txt")
    facts = RuleExtractor().extract(doc)
    kinds = by_kind(facts)

    assert doc.account == "Northwind Robotics"
    assert kinds["competitor"] == ["PipeIQ"]
    assert kinds["stakeholder"] == ["Priya Shah", "Marco Ruiz"]  # the rep is not a stakeholder
    assert "Honestly the pricing looks high for a team of forty." in kinds["objection"]
    assert any("bottleneck" in pain for pain in kinds["pain"])
    assert kinds["next_step"] == ["As a next step I will send over a pricing breakdown by seat tier."]
    competitor = next(f for f in facts if f.kind == "competitor")
    assert competitor.source.endswith("northwind-robotics.txt:8")
    assert competitor.observed_at.date().isoformat() == "2026-09-08"


def test_rep_lines_never_produce_pain_or_competitor(tmp_path):
    path = tmp_path / "call.txt"
    path.write_text("Account: X Corp\nDate: 2026-01-01\nRep: Dana\n\nDana: Unlike PipeIQ we are not slow or manual.\n")
    assert RuleExtractor().extract(parse_transcript(path)) == []


def test_transcript_without_headers_is_rejected(tmp_path):
    path = tmp_path / "call.txt"
    path.write_text("Dana: hello\n")
    with pytest.raises(ValueError, match="Account"):
        parse_transcript(path)


def test_email_uses_organization_sender_and_date():
    doc = parse_email(EMAILS / "2026-09-15-northwind-robotics.eml")
    kinds = by_kind(RuleExtractor().extract(doc))

    assert doc.account == "Northwind Robotics"
    assert doc.observed_at.isoformat() == "2026-09-15T10:12:00+00:00"
    assert kinds["stakeholder"] == ["Priya Shah"]
    assert kinds["competitor"] == ["Dealstream"]
    assert kinds["next_step"] == ["As a next step, can we book a call with your security team next Tuesday?"]


def test_crm_salesforce_columns_map_to_normalised_facts():
    facts = crm_facts(EXAMPLES / "artifacts" / "crm" / "salesforce-opportunities.csv")
    acme = {f.kind: f.value for f in facts if f.account == "Acme Freight"}
    assert acme == {"stage": "proposal", "amount": "52000", "next_step": "Finance review"}


def test_crm_hubspot_aliases_are_case_insensitive(tmp_path):
    path = tmp_path / "hubspot.csv"
    path.write_text("COMPANY NAME,Deal Stage,Amount,Last Modified Date\nAcme Freight,contractsent,1000,2026-10-01\n")
    assert read_crm_rows(path) == [
        {"account": "Acme Freight", "stage": "contractsent", "amount": "1000", "date": "2026-10-01"}
    ]
    assert {f.kind: f.value for f in crm_facts(path)}["stage"] == "negotiation"


def test_crm_without_account_column_is_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Name,Stage,Date\nx,y,2026-01-01\n")
    with pytest.raises(ValueError, match="account"):
        read_crm_rows(path)
