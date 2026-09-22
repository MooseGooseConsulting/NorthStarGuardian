"""Tests for Linear integration helpers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from guardian.linear import LinearAPIError, LinearClient, normalize_markdown


class StubLinearClient(LinearClient):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(api_key="test-key")
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((query, variables))
        return self.responses.pop(0)


def test_normalize_markdown_normalizes_line_endings_and_trailing_newline() -> None:
    assert normalize_markdown("hello\r\nworld  \r\n") == "hello\nworld\n"


def test_fetch_document_returns_snapshot_with_hash_and_metadata() -> None:
    client = StubLinearClient(
        [
            {
                "document": {
                    "id": "doc-1",
                    "title": "Repo North Star",
                    "content": "# Policy\r\n",
                    "documentContentId": "content-1",
                    "updatedAt": "2026-05-26T12:00:00.000Z",
                    "url": "https://linear.app/acme/document/doc-1",
                    "updatedBy": {"id": "user-1", "name": "Ada"},
                }
            }
        ]
    )

    snapshot = client.fetch_document("doc-1", fetched_at=datetime(2026, 5, 26, 12, 1))

    assert snapshot.provider == "linear"
    assert snapshot.document_id == "doc-1"
    assert snapshot.document_content_id == "content-1"
    assert snapshot.updated_by == "Ada"
    assert snapshot.content == "# Policy\n"
    assert len(snapshot.sha256) == 64
    assert client.calls[0][1] == {"id": "doc-1"}


def test_fetch_document_raises_for_missing_document() -> None:
    client = StubLinearClient([{"document": None}])

    with pytest.raises(LinearAPIError, match="not found"):
        client.fetch_document("missing")


def test_graphql_raises_for_errors() -> None:
    client = LinearClient(api_key="test-key")

    with pytest.raises(LinearAPIError, match="boom"):
        client._parse_response(json.dumps({"errors": [{"message": "boom"}]}))


def test_create_issue_returns_issue_metadata() -> None:
    client = StubLinearClient(
        [
            {
                "issueCreate": {
                    "success": True,
                    "issue": {
                        "id": "issue-1",
                        "identifier": "GUAR-123",
                        "title": "Drift detected",
                        "url": "https://linear.app/acme/issue/GUAR-123",
                    },
                }
            }
        ]
    )

    issue = client.create_issue(
        team_id="team-1",
        title="Drift detected",
        description="Review summary",
        project_id="project-1",
    )

    assert issue.identifier == "GUAR-123"
    assert issue.url.endswith("GUAR-123")
    assert client.calls[0][1]["input"]["teamId"] == "team-1"
    assert client.calls[0][1]["input"]["projectId"] == "project-1"


def test_constructor_with_custom_endpoint() -> None:
    """A custom endpoint overrides the class-level default."""
    client = LinearClient(api_key="key", endpoint="https://custom.example.com/graphql")
    assert client.endpoint == "https://custom.example.com/graphql"


def test_parse_response_raises_for_non_dict_payload() -> None:
    client = LinearClient(api_key="test-key")
    with pytest.raises(ValueError, match="Expected dict payload"):
        client._parse_response(json.dumps([1, 2, 3]))


def test_parse_response_raises_for_missing_data() -> None:
    client = LinearClient(api_key="test-key")
    with pytest.raises(LinearAPIError, match="missing data"):
        client._parse_response(json.dumps({"data": "not-a-dict"}))


def test_parse_response_raises_when_data_is_none() -> None:
    client = LinearClient(api_key="test-key")
    with pytest.raises(LinearAPIError, match="missing data"):
        client._parse_response(json.dumps({"data": None}))


def test_create_issue_raises_when_not_successful() -> None:
    client = StubLinearClient([{"issueCreate": {"success": False, "issue": None}}])
    with pytest.raises(LinearAPIError, match="creation failed"):
        client.create_issue(team_id="t", title="Bug", description="desc")


def test_create_issue_raises_when_issue_missing() -> None:
    client = StubLinearClient([{"issueCreate": {"success": True, "issue": None}}])
    with pytest.raises(LinearAPIError, match="no issue"):
        client.create_issue(team_id="t", title="Bug", description="desc")


def test_fetch_document_uses_display_name_when_available() -> None:
    client = StubLinearClient(
        [
            {
                "document": {
                    "id": "doc-2",
                    "title": "Doc Two",
                    "content": "body\n",
                    "documentContentId": "c-2",
                    "updatedAt": None,
                    "url": None,
                    "updatedBy": {"displayName": "Alice D.", "name": "Alice", "id": "u-2"},
                }
            }
        ]
    )
    snapshot = client.fetch_document("doc-2")
    assert snapshot.updated_by == "Alice D."


def test_graphql_raises_linear_api_error_on_http_error() -> None:
    """LinearClient.graphql converts HTTPError into LinearAPIError."""
    from io import BytesIO
    from unittest.mock import MagicMock, patch
    from urllib.error import HTTPError

    client = LinearClient(api_key="key")
    exc = HTTPError(
        url="https://api.linear.app/graphql",
        code=401,
        msg="Unauthorized",
        hdrs=MagicMock(),  # type: ignore[arg-type]
        fp=BytesIO(b"Invalid API key"),
    )
    with (
        patch("guardian.linear.urlopen", side_effect=exc),
        pytest.raises(LinearAPIError, match="401"),
    ):
        client.graphql("query { }", {})


def test_graphql_raises_linear_api_error_on_url_error() -> None:
    """LinearClient.graphql converts URLError into LinearAPIError."""
    from unittest.mock import patch
    from urllib.error import URLError

    client = LinearClient(api_key="key")
    with (
        patch("guardian.linear.urlopen", side_effect=URLError("network unreachable")),
        pytest.raises(LinearAPIError, match="request failed"),
    ):
        client.graphql("query { }", {})


def test_graphql_returns_data_on_success() -> None:
    """LinearClient.graphql parses a successful HTTP response into the data dict."""
    from contextlib import contextmanager
    from unittest.mock import patch

    payload = json.dumps({"data": {"document": {"id": "doc-99"}}}).encode()

    @contextmanager
    def fake_urlopen(request, timeout=30):  # noqa: ANN001
        class FakeResponse:
            def read(self) -> bytes:
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        yield FakeResponse()

    client = LinearClient(api_key="key")
    with patch("guardian.linear.urlopen", fake_urlopen):
        data = client.graphql("query { }", {})

    assert data == {"document": {"id": "doc-99"}}


def test_parse_response_raises_for_invalid_json() -> None:
    """_parse_response raises LinearAPIError when the raw response is not valid JSON."""
    client = LinearClient(api_key="test-key")
    with pytest.raises(LinearAPIError, match="invalid JSON"):
        client._parse_response("this is not json {{{")
