import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from conftest import EXAMPLES

from revloop.extract import RuleExtractor, parse_transcript
from revloop.llm import MODEL, ExtractionError, LLMExtractor, choose_extractor

DOC = parse_transcript(EXAMPLES / "artifacts" / "calls" / "2026-08-20-acme-freight.txt")


def fake_client(payload, stop_reason="end_turn"):
    client = MagicMock()
    text = SimpleNamespace(type="text", text=json.dumps(payload))
    client.beta.messages.create.return_value = SimpleNamespace(stop_reason=stop_reason, content=[text])
    return client


def test_llm_extractor_maps_structured_json_to_facts():
    client = fake_client(
        {
            "facts": [
                {"kind": "pain", "value": " Dispatchers re-key every load. "},
                {"kind": "competitor", "value": "PipeIQ"},
                {"kind": "made_up", "value": "ignored"},
                {"kind": "objection", "value": "  "},
            ]
        }
    )
    facts = LLMExtractor(client=client).extract(DOC)

    assert [(f.kind, f.value) for f in facts] == [("pain", "Dispatchers re-key every load."), ("competitor", "PipeIQ")]
    assert all(f.account == "Acme Freight" and f.source.endswith("(llm)") for f in facts)
    kwargs = client.beta.messages.create.call_args.kwargs
    assert kwargs["model"] == MODEL
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert "Rep) is Dana Cole" in kwargs["messages"][0]["content"]


def test_llm_extractor_raises_on_refusal_or_truncation():
    with pytest.raises(ExtractionError, match="refusal"):
        LLMExtractor(client=fake_client({}, stop_reason="refusal")).extract(DOC)


def test_auto_uses_rules_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(choose_extractor("auto"), RuleExtractor)
    assert isinstance(choose_extractor("rules"), RuleExtractor)


def test_auto_uses_llm_with_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # constructing the client makes no network call
    assert isinstance(choose_extractor("auto"), LLMExtractor)
