import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def load_fixture():
    def _load(name: str) -> str:
        with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as f:
            return f.read()
    return _load
