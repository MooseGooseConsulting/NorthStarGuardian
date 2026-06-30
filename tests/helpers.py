"""Shared in-memory test doubles for NorthStarGuardian tests.

Import these classes directly when you need to instantiate them in test helper
functions or fixtures that live outside conftest.py.  For pytest fixtures, use
``fake_store`` from conftest.py instead.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any


class FakeLLMClient:
    """In-memory LLM client double.  Returns canned responses in FIFO order.

    Usage::

        client = FakeLLMClient("resp1", "resp2")
        client.queue("resp3")
    """

    def __init__(self, *responses: str) -> None:
        self._queue: list[str] = list(responses)
        self.call_count: int = 0
        self.calls: list[dict[str, str | int]] = []

    def queue(self, response: str) -> None:
        self._queue.append(response)

    def generate(
        self,
        *,
        system: str,
        user: str,
        model: str = "test",
        max_tokens: int = 4096,
    ) -> str:
        self.call_count += 1
        self.calls.append(
            {
                "system": system,
                "user": user,
                "model": model,
                "max_tokens": max_tokens,
            }
        )
        if self._queue:
            return self._queue.pop(0)
        raise RuntimeError(
            f"FakeLLMClient: no queued response for call #{self.call_count}. "
            f"Prompt received:\n{user[:200]}"
        )

    def assert_call_count(self, expected: int) -> None:
        """Assert that *expected* calls were made."""
        actual = self.call_count
        assert actual == expected, f"Expected {expected} LLM calls, got {actual}"


class FakeStore:
    """In-memory stand-in for MemoryStore; no git, no filesystem side-effects.

    Behaves like MemoryStore but stores every file as a UTF-8 string in a
    plain dict so tests run fully offline without a git repository.
    """

    def __init__(self) -> None:
        self._files: dict[str, str] = {}
        self._initialized = True  # FakeStore is always "initialized"

    # ------------------------------------------------------------------
    # MemoryStore-compatible API
    # ------------------------------------------------------------------

    def ensure_initialized(self) -> None:
        pass

    def read(self, path: str) -> str:
        if path not in self._files:
            raise FileNotFoundError(f"fake-store:{path} does not exist")
        return self._files[path]

    def read_json(self, path: str) -> Any:
        return json.loads(self.read(path))

    def exists(self, path: str) -> bool:
        return path in self._files

    def write(self, path: str, content: str, message: str = "") -> None:
        self._files[path] = content

    def write_json(self, path: str, obj: Any, message: str = "") -> None:
        self._files[path] = json.dumps(obj, indent=2, default=str)

    def list(self, prefix: str = "") -> list[str]:
        if not prefix:
            return sorted(self._files.keys())
        return sorted(k for k in self._files if k.startswith(prefix))

    def commit_and_push(self, message: str, push: bool = True) -> None:
        pass  # no-op for in-memory store

    def __enter__(self) -> FakeStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.commit_and_push("store context exit")

    def session(self, message: str, push: bool = True):
        @contextmanager
        def _ctx():
            yield self
            self.commit_and_push(message, push=push)

        return _ctx()
