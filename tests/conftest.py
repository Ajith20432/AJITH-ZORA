"""
conftest.py — shared pytest fixtures.

The central one is `isolated_brain`, which every test that touches
strategy/brain logic should use instead of the module-level `brain.BRAIN`
singleton — it's backed by a throwaway JSON file under pytest's tmp_path,
so tests never read or write the real brain_state.json, and tests can't
pollute each other's learned weights by running in the same process.
"""
from __future__ import annotations

import os
import sys

import pytest

# Flat project layout (no package/__init__.py) — make the project root
# importable regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import AdaptiveBrain  # noqa: E402


@pytest.fixture
def isolated_brain(tmp_path) -> AdaptiveBrain:
    return AdaptiveBrain(state_path=tmp_path / "brain_state.json")
