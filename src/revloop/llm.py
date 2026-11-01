"""Optional extractor that asks Claude for facts as schema-constrained JSON."""

import json
import os

from revloop.extract import Document, Extractor, RuleExtractor
from revloop.facts import KINDS, Fact

MODEL = "claude-opus-5-5"
SYSTEM_PROMPT = (
    "You extract sales facts from a B2B sales artifact (call transcript or email). "
    "Return only facts stated by the customer, never by the seller (the 'Rep'). "
    "Kinds: pain (a problem they have), objection (a reason they hesitate), competitor (a vendor they "
    "mention evaluating or using; value is the vendor name only), next_step (an agreed follow-up), "
    "stakeholder (a customer person's name), stage and amount (only if stated explicitly). "
    "Each value is one short sentence or name, quoted or lightly trimmed from the text. "
    "If nothing fits a kind, leave it out."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kind": {"type": "string", "enum": sorted(KINDS)}, "value": {"type": "string"}},
                "required": ["kind", "value"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}


class ExtractionError(RuntimeError):
    pass


class LLMExtractor:
    def __init__(self, client=None, model: str = MODEL):
        if client is None:
            try:
                import anthropic
            except ImportError as error:
                raise ExtractionError("the LLM extractor needs the 'llm' extra: uv sync --extra llm") from error
            client = anthropic.Anthropic()
        self._client = client
        self._model = model

    def extract(self, doc: Document) -> list[Fact]:
        rep_note = f"The seller (Rep) is {doc.rep}.\n\n" if doc.rep else ""
        response = self._client.beta.messages.create(
            model=self._model,
            max_tokens=16000,
            # If the model declines, the API retries on a fallback model inside the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": rep_note + doc.text()}],
        )
        if response.stop_reason != "end_turn":
            raise ExtractionError(f"{doc.source}: model stopped with '{response.stop_reason}'")
        text = next(block.text for block in response.content if block.type == "text")
        return [
            Fact(doc.account, item["kind"], item["value"].strip(), f"{doc.source} (llm)", doc.observed_at)
            for item in json.loads(text)["facts"]
            if item["kind"] in KINDS and item["value"].strip()
        ]


def choose_extractor(name: str = "auto") -> Extractor:
    """'auto' uses the LLM only when ANTHROPIC_API_KEY is set; otherwise the offline rules."""
    if name == "llm" or (name == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return LLMExtractor()
    return RuleExtractor()
