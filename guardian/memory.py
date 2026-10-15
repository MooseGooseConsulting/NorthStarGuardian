"""Repo-native Guardian state I/O.

Guardian-owned state is stored inside the normal checkout under
``.github/guardian``.  The store deliberately avoids extra branches, git
checkouts, and implicit pushes; callers that want to persist generated files
must commit the changed checkout through their usual repo workflow.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class MemoryError(Exception):
    """Base for memory-layer errors."""


class MemoryNotInitialized(MemoryError):
    """Raised when a read is attempted before the Guardian store exists."""


class MemoryStore:
    """Read/write interface for ``.github/guardian`` repo-native state."""

    def __init__(
        self,
        repo_root: Path,
        storage_path: Path | None = None,
    ) -> None:
        self.repo_root = repo_root
        if storage_path is not None and not storage_path.is_absolute():
            self.storage_path = (repo_root / storage_path).resolve()
        else:
            self.storage_path = storage_path or repo_root / ".github" / "guardian"
        self._root: Path = self.storage_path.resolve()
        self._ready = False

    def ensure_initialized(self) -> None:
        """Create ``.github/guardian`` if needed."""
        if self._ready:
            return
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._root = self.storage_path.resolve()
        self._ready = True

    def _require_ready(self) -> None:
        if not self._ready:
            raise MemoryNotInitialized(
                "Call ensure_initialized() before reading or writing Guardian state."
            )

    def _resolve(self, path: str) -> Path:
        self._require_ready()
        relative = Path(path)
        if relative.is_absolute():
            raise ValueError("Guardian storage paths must be relative")
        full = (self._root / relative).resolve()
        if full != self._root and self._root not in full.parents:
            raise ValueError(f"Path '{path}' escapes outside Guardian storage")
        return full

    def read(self, path: str) -> str:
        """Return the text content of *path* from ``.github/guardian``."""
        full = self._resolve(path)
        if not full.exists():
            raise FileNotFoundError(f".github/guardian:{path} does not exist")
        return full.read_text(encoding="utf-8")

    def read_json(self, path: str) -> Any:
        """Return the parsed JSON content of *path*."""
        return json.loads(self.read(path))

    def exists(self, path: str) -> bool:
        """Return True if *path* exists in ``.github/guardian``."""
        return self._resolve(path).exists()

    def write(self, path: str, content: str, message: str = "") -> None:
        """Write *content* to *path* in ``.github/guardian`` atomically.

        ``message`` is accepted for API compatibility with the old batched
        storage interface; repo-native writes do not commit automatically.
        """
        _ = message
        full = self._resolve(path)
        parent = full.parent
        parent.mkdir(parents=True, exist_ok=True)

        # Atomic write-replace pattern
        fd, temp_path = tempfile.mkstemp(dir=parent, prefix=f"{full.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, full)
            if os.name == "posix":
                dir_fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        except Exception as exc:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise MemoryError(f"Failed to write to {path}: {exc}") from exc

    def write_json(self, path: str, obj: Any, message: str = "") -> None:
        """Serialise *obj* as pretty-printed JSON and write it."""
        self.write(path, json.dumps(obj, indent=2, default=str), message)

    def list(self, prefix: str = "") -> list[str]:
        """Return relative paths of all files, optionally filtered by *prefix*."""
        base = self._resolve(prefix) if prefix else self._resolve(".")
        if not base.exists():
            return []

        if base.is_file():
            return [str(base.relative_to(self._root)).replace("\\", "/")]

        results: list[str] = []
        for p in sorted(base.rglob("*")):
            if p.is_file():
                rel = p.relative_to(self._root)
                results.append(str(rel).replace("\\", "/"))
        return results

    def commit_and_push(self, message: str, push: bool = True) -> None:
        """No-op compatibility boundary for repo-native storage."""
        _ = (message, push)
        return None

    @contextmanager
    def session(self, message: str, push: bool = True) -> Generator[MemoryStore, None, None]:
        """Yield this store without creating a separate commit/push."""
        _ = (message, push)
        yield self
