"""
Test suite for Grok API response validation
Tests the critical validation fixes implemented in grok_client.py
"""
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from unittest.mock import Mock, patch, MagicMock
import pytest

from app.llm.grok_client import (
    send_chat,
    stream_chat,
    astream_chat,
    astream_chat_iter,
    GrokAPIError
)


class TestSendChatValidation:
    """Test send_chat response validation"""

    def test_null_response_raises_error(self):
        """Test that null response raises GrokAPIError"""
        with patch('app.llm.grok_client._get_sync_client') as mock_client:
            mock_instance = Mock()
            mock_instance.chat.completions.create.return_value = None
            mock_client.return_value = mock_instance

            with pytest.raises(GrokAPIError, match="null response"):
                send_chat([{"role": "user", "content": "test"}])

    def test_empty_choices_raises_error(self):
        """Test that empty choices array raises GrokAPIError"""
        with patch('app.llm.grok_client._get_sync_client') as mock_client:
            mock_instance = Mock()
            mock_response = Mock()
            mock_response.choices = []
            mock_instance.chat.completions.create.return_value = mock_response
            mock_client.return_value = mock_instance

            with pytest.raises(GrokAPIError, match="missing choices"):
                send_chat([{"role": "user", "content": "test"}])

    def test_missing_choices_raises_error(self):
        """Test that missing choices attribute raises GrokAPIError"""
        with patch('app.llm.grok_client._get_sync_client') as mock_client:
            mock_instance = Mock()
            mock_response = Mock(spec=[])  # No 'choices' attribute
            mock_instance.chat.completions.create.return_value = mock_response
            mock_client.return_value = mock_instance

            with pytest.raises(GrokAPIError, match="missing choices"):
                send_chat([{"role": "user", "content": "test"}])

    def test_null_content_raises_error(self):
        """Test that null content raises GrokAPIError"""
        with patch('app.llm.grok_client._get_sync_client') as mock_client:
            mock_instance = Mock()
            mock_response = Mock()
            mock_choice = Mock()
            mock_message = Mock()
            mock_message.content = None
            mock_choice.message = mock_message
            mock_response.choices = [mock_choice]
            mock_instance.chat.completions.create.return_value = mock_response
            mock_client.return_value = mock_instance

            with pytest.raises(GrokAPIError, match="content is null"):
                send_chat([{"role": "user", "content": "test"}])

    def test_empty_content_raises_error(self):
        """Test that empty/whitespace content raises GrokAPIError"""
        with patch('app.llm.grok_client._get_sync_client') as mock_client:
            mock_instance = Mock()
            mock_response = Mock()
            mock_choice = Mock()
            mock_message = Mock()
            mock_message.content = "   "  # Whitespace only
            mock_choice.message = mock_message
            mock_response.choices = [mock_choice]
            mock_instance.chat.completions.create.return_value = mock_response
            mock_client.return_value = mock_instance

            with pytest.raises(GrokAPIError, match="empty or whitespace"):
                send_chat([{"role": "user", "content": "test"}])

    def test_non_string_content_raises_error(self):
        """Test that non-string content raises GrokAPIError"""
        with patch('app.llm.grok_client._get_sync_client') as mock_client:
            mock_instance = Mock()
            mock_response = Mock()
            mock_choice = Mock()
            mock_message = Mock()
            mock_message.content = 12345  # Integer instead of string
            mock_choice.message = mock_message
            mock_response.choices = [mock_choice]
            mock_instance.chat.completions.create.return_value = mock_response
            mock_client.return_value = mock_instance

            with pytest.raises(GrokAPIError, match="invalid type"):
                send_chat([{"role": "user", "content": "test"}])

    def test_valid_response_returns_content(self):
        """Test that valid response returns stripped content"""
        with patch('app.llm.grok_client._get_sync_client') as mock_client:
            mock_instance = Mock()
            mock_response = Mock()
            mock_choice = Mock()
            mock_message = Mock()
            mock_message.content = "  Valid response content  "
            mock_choice.message = mock_message
            mock_response.choices = [mock_choice]
            mock_instance.chat.completions.create.return_value = mock_response
            mock_client.return_value = mock_instance

            result = send_chat([{"role": "user", "content": "test"}])

            assert result == "Valid response content"
            assert isinstance(result, str)


class TestStreamChatValidation:
    """Test stream_chat response validation"""

    def test_empty_stream_raises_error(self):
        """Test that stream with no content raises GrokAPIError"""
        with patch('app.llm.grok_client._get_sync_client') as mock_client:
            mock_instance = Mock()
            # Empty iterator
            mock_instance.chat.completions.create.return_value = iter([])
            mock_client.return_value = mock_instance

            with pytest.raises(GrokAPIError, match="no content received"):
                list(stream_chat([{"role": "user", "content": "test"}]))

    def test_stream_with_empty_choices(self):
        """Test that stream gracefully handles chunks with empty choices"""
        with patch('app.llm.grok_client._get_sync_client') as mock_client:
            mock_instance = Mock()

            # Create chunks: empty choices, then valid content
            chunk1 = Mock()
            chunk1.choices = []

            chunk2 = Mock()
            delta2 = Mock()
            delta2.content = "Valid content"
            choice2 = Mock()
            choice2.delta = delta2
            chunk2.choices = [choice2]

            mock_instance.chat.completions.create.return_value = iter([chunk1, chunk2])
            mock_client.return_value = mock_instance

            result = list(stream_chat([{"role": "user", "content": "test"}]))

            assert len(result) == 1
            assert result[0] == "Valid content"

    def test_stream_yields_valid_chunks(self):
        """Test that stream yields valid content chunks"""
        with patch('app.llm.grok_client._get_sync_client') as mock_client:
            mock_instance = Mock()

            # Create multiple valid chunks
            chunks = []
            for i, text in enumerate(["Hello ", "world", "!"]):
                chunk = Mock()
                delta = Mock()
                delta.content = text
                choice = Mock()
                choice.delta = delta
                chunk.choices = [choice]
                chunks.append(chunk)

            mock_instance.chat.completions.create.return_value = iter(chunks)
            mock_client.return_value = mock_instance

            result = list(stream_chat([{"role": "user", "content": "test"}]))

            assert len(result) == 3
            assert "".join(result) == "Hello world!"

    def test_stream_interrupted_yields_error_message(self):
        """Test that interrupted stream yields error message if partial content received"""
        with patch('app.llm.grok_client._get_sync_client') as mock_client:
            mock_instance = Mock()

            # Simulate connection error after first chunk
            import httpx
            from openai import APIConnectionError as _APIConnError

            def stream_generator():
                # First chunk succeeds
                chunk1 = Mock()
                delta1 = Mock()
                delta1.content = "Partial content"
                choice1 = Mock()
                choice1.delta = delta1
                chunk1.choices = [choice1]
                yield chunk1

                # Then connection error (requires request kwarg)
                raise _APIConnError(
                    message="Connection lost",
                    request=httpx.Request("POST", "https://api.x.ai/v1/chat/completions"),
                )

            mock_instance.chat.completions.create.return_value = stream_generator()
            mock_client.return_value = mock_instance

            result = []
            with pytest.raises(_APIConnError):
                for chunk in stream_chat([{"role": "user", "content": "test"}]):
                    result.append(chunk)

            # Should have received partial content + error message
            assert len(result) == 2
            assert result[0] == "Partial content"
            assert "การเชื่อมต่อขัดข้อง" in result[1]


class TestAsyncValidation:
    """Test async function validation (astream_chat)"""

    @pytest.mark.asyncio
    async def test_async_null_content_raises_error(self):
        """Test that async null content raises GrokAPIError"""
        with patch('app.llm.grok_client._get_async_client') as mock_client:
            mock_instance = Mock()
            mock_response = Mock()
            mock_choice = Mock()
            mock_message = Mock()
            mock_message.content = None
            mock_choice.message = mock_message
            mock_response.choices = [mock_choice]

            # Make the create method return an awaitable
            async def mock_create(**kwargs):
                return mock_response

            mock_instance.chat.completions.create = mock_create
            mock_client.return_value = mock_instance

            with pytest.raises(GrokAPIError, match="content is null"):
                await astream_chat([{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_async_valid_response_returns_content(self):
        """Test that async valid response returns stripped content"""
        with patch('app.llm.grok_client._get_async_client') as mock_client:
            mock_instance = Mock()
            mock_response = Mock()
            mock_choice = Mock()
            mock_message = Mock()
            mock_message.content = "  Valid async response  "
            mock_choice.message = mock_message
            mock_response.choices = [mock_choice]

            # Make the create method return an awaitable
            async def mock_create(**kwargs):
                return mock_response

            mock_instance.chat.completions.create = mock_create
            mock_client.return_value = mock_instance

            result = await astream_chat([{"role": "user", "content": "test"}])

            assert result == "Valid async response"
            assert isinstance(result, str)


def test_grok_api_error_inheritance():
    """Test that GrokAPIError is a proper Exception subclass"""
    error = GrokAPIError("Test error")

    assert isinstance(error, Exception)
    assert str(error) == "Test error"


if __name__ == "__main__":
    print("Running Grok API validation tests...")
    print("\nTo run these tests, install pytest and pytest-asyncio:")
    print("  pip install pytest pytest-asyncio")
    print("\nThen run:")
    print("  pytest tests/test_grok_validation.py -v")
    print("\n" + "="*60)
    print("Manual validation checks:")
    print("="*60)

    # Manual check for import
    try:
        from app.llm.grok_client import GrokAPIError
        print("✓ GrokAPIError imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import GrokAPIError: {e}")

    # Manual check for exception handling
    try:
        raise GrokAPIError("Test validation error")
    except GrokAPIError as e:
        print(f"✓ GrokAPIError raised and caught: {e}")
    except Exception as e:
        print(f"✗ Unexpected exception type: {type(e)}")

    print("\n✓ Basic validation checks passed!")
    print("Run pytest for comprehensive testing.")
