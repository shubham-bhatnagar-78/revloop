from pathlib import Path

import pytest

from revloop.store import Store

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
PLAYBOOKS = ROOT / "playbooks"


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s
