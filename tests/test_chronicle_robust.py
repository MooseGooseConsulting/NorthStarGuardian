from unittest.mock import MagicMock

from guardian.chronicle import _load_journal_entry
from guardian.memory import MemoryStore


def test_load_journal_entry_robust_date():
    content = """---
pr_number: 123
timestamp: 2024-06-30 14:00:00 UTC
verdict: aligned
---
Body content"""
    mock_store = MagicMock(spec=MemoryStore)
    mock_store.read.return_value = content
    entry = _load_journal_entry(mock_store, "dummy.md")
    assert entry is not None
    assert entry.pr_number == 123
    assert entry.timestamp.year == 2024

def test_load_journal_entry_malformed():
    mock_store = MagicMock(spec=MemoryStore)
    mock_store.read.return_value = "not a journal"
    entry = _load_journal_entry(mock_store, "dummy.md")
    assert entry is None
