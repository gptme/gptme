"""Unit tests for trycompai/crm integration tool."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from gptme.tools.crm import (
    TrycompaiCrmClient,
    TrycompaiCrmError,
    trycompai_crm_execute,
)


class TestTrycompaiCrmClient:
    """Test TrycompaiCrmClient HTTP interactions."""

    def test_init_with_env_key(self):
        """Client initializes with API key from environment."""
        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key_123"}):
            client = TrycompaiCrmClient()
            assert client.api_key == "test_key_123"
            client.close()

    def test_init_with_missing_key(self):
        """Client raises error if no API key provided."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(TrycompaiCrmError) as excinfo:
                TrycompaiCrmClient()
            assert "API key not provided" in str(excinfo.value)

    def test_init_with_custom_url(self):
        """Client respects custom API URL."""
        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            client = TrycompaiCrmClient(api_url="https://staging.trycompai.com")
            assert client.api_url == "https://staging.trycompai.com"
            client.close()

    def test_init_strips_trailing_slash(self):
        """Client normalizes API URL (removes trailing slash)."""
        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            client = TrycompaiCrmClient(api_url="https://api.trycompai.com/")
            assert client.api_url == "https://api.trycompai.com"
            client.close()

    @patch("gptme.tools.crm.httpx.Client.get")
    def test_search_crm_success(self, mock_get):
        """search_crm makes correct API request."""
        mock_get.return_value = MagicMock(
            json=lambda: {
                "results": [
                    {
                        "id": "c1",
                        "name": "Alice",
                        "type": "contact",
                        "match_score": 0.95,
                    }
                ]
            }
        )

        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            client = TrycompaiCrmClient()
            result = client.search_crm(query="alice", filter_type="contact")

            assert result["results"][0]["name"] == "Alice"
            mock_get.assert_called_once()
            client.close()

    @patch("gptme.tools.crm.httpx.Client.post")
    def test_identify_contact_success(self, mock_post):
        """identify_contact makes correct API request."""
        mock_post.return_value = MagicMock(
            json=lambda: {
                "contact_id": "c123",
                "confidence": 0.98,
                "evidence": "Exact email match",
            }
        )

        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            client = TrycompaiCrmClient()
            result = client.identify_contact(email="alice@acme.com")

            assert result["contact_id"] == "c123"
            assert result["confidence"] == 0.98
            mock_post.assert_called_once()
            client.close()

    def test_identify_contact_requires_identifier(self):
        """identify_contact requires at least one identifier."""
        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            client = TrycompaiCrmClient()

            with pytest.raises(TrycompaiCrmError) as excinfo:
                client.identify_contact()
            assert "identifier" in str(excinfo.value)
            client.close()

    @patch("gptme.tools.crm.httpx.Client.post")
    def test_record_fact_success(self, mock_post):
        """record_fact makes correct API request."""
        mock_post.return_value = MagicMock(
            json=lambda: {
                "fact_id": "f456",
                "recorded_at": "2026-08-06T15:00:00Z",
            }
        )

        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            client = TrycompaiCrmClient()
            result = client.record_fact(
                contact_id="c123",
                key="company_name",
                value="Acme Inc",
                evidence="From email signature",
            )

            assert result["fact_id"] == "f456"
            # Verify no confidence score was sent
            call_args = mock_post.call_args
            data = call_args.kwargs["json"]
            assert "confidence" not in data
            client.close()

    @patch("gptme.tools.crm.httpx.Client.post")
    def test_enrich_company_success(self, mock_post):
        """enrich_company makes correct API request."""
        mock_post.return_value = MagicMock(
            json=lambda: {
                "company_name": "Acme Inc",
                "founding_date": "2015",
                "stage": "Series B",
                "headcount": 150,
                "industry": "Software",
                "ceo": "John Doe",
            }
        )

        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            client = TrycompaiCrmClient()
            result = client.enrich_company(company_name="Acme Inc")

            assert result["stage"] == "Series B"
            assert result["headcount"] == 150
            client.close()

    @patch("gptme.tools.crm.httpx.Client.post")
    def test_schedule_recheck_success(self, mock_post):
        """schedule_recheck makes correct API request."""
        mock_post.return_value = MagicMock(
            json=lambda: {
                "task_id": "t789",
                "due_at": "2026-08-13T00:00:00Z",
            }
        )

        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            client = TrycompaiCrmClient()
            result = client.schedule_recheck(
                contact_id="c123",
                reason="Follow up on Q3 hiring",
                due_in_days=7,
            )

            assert result["task_id"] == "t789"
            client.close()

    @patch("gptme.tools.crm.httpx.Client.get")
    def test_read_crm_history_success(self, mock_get):
        """read_crm_history makes correct API request."""
        mock_get.return_value = MagicMock(
            json=lambda: {
                "interactions": [
                    {
                        "id": "i1",
                        "type": "email",
                        "timestamp": "2026-08-01T10:00:00Z",
                        "summary": "Initial inquiry",
                    }
                ]
            }
        )

        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            client = TrycompaiCrmClient()
            result = client.read_crm_history(contact_id="c123", limit=10)

            assert len(result["interactions"]) == 1
            client.close()

    @patch("gptme.tools.crm.httpx.Client.get")
    def test_api_error_handling(self, mock_get):
        """Client handles HTTP errors gracefully."""
        mock_get.side_effect = httpx.HTTPError("Network error")

        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            client = TrycompaiCrmClient()

            with pytest.raises(TrycompaiCrmError) as excinfo:
                client.search_crm(query="test")
            assert "API request failed" in str(excinfo.value)
            client.close()


class TestTrycompaiCrmTool:
    """Test trycompai_crm_tool function (gptme Tool wrapper)."""

    @patch("gptme.tools.crm.TrycompaiCrmClient.search_crm")
    def test_tool_search_crm_operation(self, mock_search):
        """Tool correctly dispatches search_crm operation."""
        mock_search.return_value = {
            "results": [{"id": "c1", "name": "Alice", "type": "contact"}]
        }

        args = {"operation": "search_crm", "query": "alice"}
        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            output = list(trycompai_crm_execute(json.dumps(args), None, None))

        assert len(output) > 0
        output_str = output[0].content
        assert "Alice" in output_str or "results" in output_str

    @patch("gptme.tools.crm.TrycompaiCrmClient.identify_contact")
    def test_tool_identify_contact_operation(self, mock_identify):
        """Tool correctly dispatches identify_contact operation."""
        mock_identify.return_value = {"contact_id": "c123", "confidence": 0.95}

        args = {"operation": "identify_contact", "email": "alice@acme.com"}
        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            output = list(trycompai_crm_execute(json.dumps(args), None, None))

        assert len(output) > 0
        output_str = output[0].content
        assert "c123" in output_str or "confidence" in output_str

    @patch("gptme.tools.crm.TrycompaiCrmClient.record_fact")
    def test_tool_record_fact_operation(self, mock_record):
        """Tool correctly dispatches record_fact operation."""
        mock_record.return_value = {
            "fact_id": "f456",
            "recorded_at": "2026-08-06T15:00:00Z",
        }

        args = {
            "operation": "record_fact",
            "contact_id": "c123",
            "key": "company_name",
            "value": "Acme Inc",
            "evidence": "From email",
        }
        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            output = list(trycompai_crm_execute(json.dumps(args), None, None))

        assert len(output) > 0
        output_str = output[0].content
        assert "f456" in output_str or "fact_id" in output_str

    def test_tool_invalid_json(self):
        """Tool handles invalid JSON gracefully."""
        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            output = list(trycompai_crm_execute("not valid json", None, None))

        assert len(output) > 0
        assert "Error" in output[0].content
        assert "JSON" in output[0].content

    def test_tool_missing_operation(self):
        """Tool requires operation field."""
        args = {"query": "test"}  # missing operation
        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            output = list(trycompai_crm_execute(json.dumps(args), None, None))

        assert len(output) > 0
        assert "Error" in output[0].content
        assert "operation" in output[0].content.lower()

    def test_tool_unknown_operation(self):
        """Tool rejects unknown operations."""
        args = {"operation": "unknown_op"}
        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            output = list(trycompai_crm_execute(json.dumps(args), None, None))

        assert len(output) > 0
        assert "Error" in output[0].content
        assert "Unknown operation" in output[0].content

    @patch("gptme.tools.crm.TrycompaiCrmClient")
    def test_tool_handles_client_error(self, mock_client_class):
        """Tool handles TrycompaiCrmError gracefully."""
        mock_client_class.side_effect = TrycompaiCrmError("API key invalid")

        args = {"operation": "search_crm", "query": "test"}
        with patch.dict("os.environ", {"TRYCOMPAI_API_KEY": "test_key"}):
            output = list(trycompai_crm_execute(json.dumps(args), None, None))

        assert len(output) > 0
        assert "Error" in output[0].content
        assert "API key" in output[0].content


def test_tool_spec_is_defined():
    """ToolSpec is properly defined and accessible."""
    from gptme.tools.crm import tool

    assert tool.name == "trycompai_crm"
    assert "trycompai" in tool.desc.lower() or "crm" in tool.desc.lower()
    assert tool.available is True
    assert tool.execute is not None
    assert "trycompai_crm" in tool.block_types
