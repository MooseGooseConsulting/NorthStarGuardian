"""Shared test fixtures for NorthStarGuardian tests."""

from __future__ import annotations

import pytest

from tests.helpers import FakeLLMClient, FakeStore

__all__ = ["FakeLLMClient", "FakeStore"]


@pytest.fixture
def fake_store() -> FakeStore:
    """Return a fresh FakeStore for each test."""
    return FakeStore()
