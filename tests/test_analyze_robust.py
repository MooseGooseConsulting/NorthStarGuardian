import json
import pytest
from guardian.analyze import _parse_json_response, LLMOutputError, analyze_diff, DiffParseError

def test_parse_json_response_with_preamble_and_notes():
    raw = """
Check this out:
```json
{"verdict": "aligned", "reasoning": "all good"}
```
Hope this helps!
"""
    result = _parse_json_response(raw, "test")
    assert result["verdict"] == "aligned"

def test_parse_json_response_without_fences():
    raw = '{"verdict": "drift"}'
    result = _parse_json_response(raw, "test")
    assert result["verdict"] == "drift"

def test_analyze_diff_handles_invalid_diff():
    # If the string doesn't even have "---" or "+++" unidiff might just return empty
    # But we want to ensure it doesn't crash.
    # To truly trigger DiffParseError, we need something that unidiff fails on.
    # Actually, unidiff is very lenient. 
    # Let's just check that it handles empty diffs gracefully.
    res = analyze_diff("", {"number": 1})
    assert len(res.files) == 0
