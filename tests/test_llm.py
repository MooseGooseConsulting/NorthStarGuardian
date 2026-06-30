"""Tests for guardian/llm.py.

Covers _extract_text, AnthropicLLMClient.generate, and LLMClient protocol
conformance — no real network calls required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from guardian.llm import AnthropicLLMClient, _extract_text

# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------


class TestExtractText:
    def _msg(self, blocks: list[Any]) -> Any:
        """Build a fake Anthropic message object with a .content attribute."""
        msg = MagicMock()
        msg.content = blocks
        return msg

    def _text_block(self, text: str) -> Any:
        block = MagicMock()
        block.text = text
        return block

    def _non_text_block(self) -> Any:
        block = MagicMock(spec=[])  # no .text attribute
        return block

    def test_single_text_block(self) -> None:
        block = self._text_block("Hello world")
        result = _extract_text(self._msg([block]))
        assert result == "Hello world"

    def test_multiple_text_blocks_joined(self) -> None:
        blocks = [self._text_block("first"), self._text_block("second")]
        result = _extract_text(self._msg(blocks))
        assert result == "first\nsecond"

    def test_empty_content_list(self) -> None:
        result = _extract_text(self._msg([]))
        assert result == ""

    def test_no_content_attribute(self) -> None:
        msg = object()  # no .content attr
        result = _extract_text(msg)
        assert result == ""

    def test_dict_block_with_type_text(self) -> None:
        block = {"type": "text", "text": "dict content"}
        result = _extract_text(self._msg([block]))
        assert result == "dict content"

    def test_dict_block_non_text_type_ignored(self) -> None:
        block = {"type": "image", "data": "..."}
        result = _extract_text(self._msg([block]))
        assert result == ""

    def test_mixed_object_and_dict_blocks(self) -> None:
        obj_block = self._text_block("from object")
        dict_block = {"type": "text", "text": "from dict"}
        result = _extract_text(self._msg([obj_block, dict_block]))
        assert result == "from object\nfrom dict"

    def test_empty_string_blocks_omitted(self) -> None:
        blocks = [self._text_block(""), self._text_block("real content")]
        result = _extract_text(self._msg(blocks))
        assert result == "real content"

    def test_whitespace_stripped(self) -> None:
        result = _extract_text(self._msg([self._text_block("  trimmed  ")]))
        assert result == "trimmed"


# ---------------------------------------------------------------------------
# AnthropicLLMClient
# ---------------------------------------------------------------------------


class TestAnthropicLLMClient:
    def _make_response(self, text: str) -> Any:
        block = MagicMock()
        block.text = text
        msg = MagicMock()
        msg.content = [block]
        return msg

    def test_generate_returns_text(self) -> None:
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.return_value = self._make_response("output text")

        client = AnthropicLLMClient(api_key="test-key", client=mock_anthropic)
        result = client.generate(
            system="You are a helper.",
            user="Say hello.",
            model="claude-3-haiku-20240307",
        )

        assert result == "output text"

    def test_generate_passes_correct_args(self) -> None:
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.return_value = self._make_response("ok")

        client = AnthropicLLMClient(api_key="test-key", client=mock_anthropic)
        client.generate(
            system="sys",
            user="usr",
            model="claude-model",
            max_tokens=512,
        )

        mock_anthropic.messages.create.assert_called_once_with(
            model="claude-model",
            system="sys",
            max_tokens=512,
            messages=[{"role": "user", "content": "usr"}],
        )

    def test_generate_uses_injected_client(self) -> None:
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.return_value = self._make_response("injected")

        client = AnthropicLLMClient(api_key="irrelevant", client=mock_anthropic)
        result = client.generate(system="s", user="u", model="m")

        assert result == "injected"
        mock_anthropic.messages.create.assert_called_once()

    def test_creates_real_anthropic_client_when_not_injected(self) -> None:
        with patch("guardian.llm.Anthropic") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.messages.create.return_value = self._make_response("created")
            mock_cls.return_value = mock_instance

            client = AnthropicLLMClient(api_key="my-key")
            mock_cls.assert_called_once_with(api_key="my-key")
            assert client._client is mock_instance

    def test_max_tokens_default(self) -> None:
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.return_value = self._make_response("ok")

        client = AnthropicLLMClient(api_key="k", client=mock_anthropic)
        client.generate(system="s", user="u", model="m")

        call_kwargs = mock_anthropic.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 4096


# ---------------------------------------------------------------------------
# LLMClient protocol conformance
# ---------------------------------------------------------------------------


class TestLLMClientProtocol:
    def test_fake_llm_client_satisfies_protocol(self) -> None:
        """FakeLLMClient from helpers should satisfy the LLMClient protocol."""
        from tests.helpers import FakeLLMClient

        fake = FakeLLMClient("response")
        # Protocol requires generate(**kwargs) -> str
        result = fake.generate(system="s", user="u", model="m")
        assert isinstance(result, str)

    def test_anthropic_client_satisfies_protocol(self) -> None:
        """AnthropicLLMClient satisfies the LLMClient protocol at runtime."""
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.return_value = MagicMock(content=[])

        client = AnthropicLLMClient(api_key="k", client=mock_anthropic)
        # Protocol check: must have generate method with correct signature
        assert callable(client.generate)
        result = client.generate(system="s", user="u", model="m")
        assert isinstance(result, str)
