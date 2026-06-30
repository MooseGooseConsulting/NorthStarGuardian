"""Linear API helpers for Guardian policy and review records."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel


class LinearAPIError(RuntimeError):
    """Raised when Linear returns an error or an unexpected response."""


class LinearDocumentSnapshot(BaseModel):
    """The exact Linear document content Guardian used for a run."""

    provider: Literal["linear"] = "linear"
    document_id: str
    document_content_id: str | None = None
    title: str
    url: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None
    fetched_at: datetime
    sha256: str
    content: str


class LinearIssue(BaseModel):
    """Linear issue metadata returned after creating a Guardian follow-up."""

    id: str
    identifier: str
    title: str
    url: str


def normalize_markdown(text: str) -> str:
    """Normalize Markdown before hashing or parsing."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized = "\n".join(line.rstrip() for line in lines).rstrip()
    return f"{normalized}\n" if normalized else ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LinearClient:
    """Small GraphQL client for the Linear surfaces Guardian needs."""

    endpoint = "https://api.linear.app/graphql"

    def __init__(self, api_key: str, endpoint: str | None = None) -> None:
        self.api_key = api_key
        if endpoint:
            self.endpoint = endpoint

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Execute a Linear GraphQL query and return the ``data`` object."""
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "NorthStarGuardian",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LinearAPIError(f"Linear API HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise LinearAPIError(f"Linear API request failed: {exc.reason}") from exc
        return self._parse_response(raw)

    def _parse_response(self, raw: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LinearAPIError("Linear API returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise ValueError(f"Expected dict payload, got {type(payload).__name__}")

        errors = payload.get("errors")
        if errors:
            message = "; ".join(str(err.get("message", err)) for err in errors)
            raise LinearAPIError(message)

        data = payload.get("data")
        if not isinstance(data, dict):
            raise LinearAPIError("Linear API response was missing data")
        return data

    def fetch_document(
        self,
        document_id: str,
        *,
        fetched_at: datetime | None = None,
    ) -> LinearDocumentSnapshot:
        """Fetch a Linear document and return Guardian's normalized snapshot."""
        data = self.graphql(_DOCUMENT_QUERY, {"id": document_id})
        doc = data.get("document")
        if not isinstance(doc, dict):
            raise LinearAPIError(f"Linear document '{document_id}' was not found")

        content = normalize_markdown(str(doc.get("content") or ""))
        updated_by = doc.get("updatedBy")
        updated_by_name = None
        if isinstance(updated_by, dict):
            updated_by_name = (
                updated_by.get("displayName") or updated_by.get("name") or updated_by.get("id")
            )

        return LinearDocumentSnapshot(
            document_id=str(doc.get("id") or document_id),
            document_content_id=doc.get("documentContentId"),
            title=str(doc.get("title") or ""),
            url=doc.get("url"),
            updated_at=doc.get("updatedAt"),
            updated_by=updated_by_name,
            fetched_at=fetched_at or datetime.now(tz=UTC),
            sha256=_sha256(content),
            content=content,
        )

    def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        project_id: str | None = None,
    ) -> LinearIssue:
        """Create a Linear issue for a concrete Guardian follow-up."""
        issue_input: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "description": description,
        }
        if project_id:
            issue_input["projectId"] = project_id

        data = self.graphql(_ISSUE_CREATE_MUTATION, {"input": issue_input})
        result = data.get("issueCreate")
        if not isinstance(result, dict) or not result.get("success"):
            raise LinearAPIError("Linear issue creation failed")
        issue = result.get("issue")
        if not isinstance(issue, dict):
            raise LinearAPIError("Linear issue creation returned no issue")

        return LinearIssue(
            id=str(issue["id"]),
            identifier=str(issue["identifier"]),
            title=str(issue["title"]),
            url=str(issue["url"]),
        )


_DOCUMENT_QUERY = """
query GuardianDocument($id: String!) {
  document(id: $id) {
    id
    title
    content
    documentContentId
    updatedAt
    url
    updatedBy {
      id
      name
      displayName
      email
    }
  }
}
"""

_ISSUE_CREATE_MUTATION = """
mutation GuardianIssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue {
      id
      identifier
      title
      url
    }
  }
}
"""
