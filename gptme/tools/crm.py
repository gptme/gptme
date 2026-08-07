"""trycompai/crm integration for gptme.

Exposes core trycompai CRM operations (search, identify, record facts, enrich,
schedule follow-ups) as a gptme Tool, enabling autonomous agents to orchestrate
CRM workflows within gptme sessions.

Architecture:
- Tool wraps trycompai/crm's 18 core agent operations
- Auth: API key stored in config/env (TRYCOMPAI_API_KEY, TRYCOMPAI_API_URL)
- Semantics: record_fact intentionally rejects confidence scores (follows trycompai rule)
- Responses: Mirror trycompai shapes for clarity (not a generic REST wrapper)
"""

import json
import logging
import os
from collections.abc import Generator
from typing import Any

import httpx

from ..message import Message
from .base import ToolSpec

logger = logging.getLogger(__name__)

# Configuration
DEFAULT_API_URL = "https://api.trycompai.com"
API_KEY_ENV = "TRYCOMPAI_API_KEY"
API_URL_ENV = "TRYCOMPAI_API_URL"


class TrycompaiCrmError(Exception):
    """Base exception for trycompai CRM operations."""


class TrycompaiCrmClient:
    """Client for trycompai/crm HTTP API.

    Handles authentication, request building, and error handling.
    """

    def __init__(self, api_key: str | None = None, api_url: str | None = None):
        """Initialize the CRM client.

        Args:
            api_key: Trycompai API key. Defaults to TRYCOMPAI_API_KEY env var.
            api_url: Trycompai API base URL. Defaults to TRYCOMPAI_API_URL env var or production.
        """
        self.api_key = api_key or os.environ.get(API_KEY_ENV)
        self.api_url = (
            api_url or os.environ.get(API_URL_ENV) or DEFAULT_API_URL
        ).rstrip("/")

        if not self.api_key:
            raise TrycompaiCrmError(
                f"Trycompai API key not provided. "
                f"Set {API_KEY_ENV} environment variable or pass api_key parameter."
            )

        self.client = httpx.Client(
            base_url=self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "gptme-trycompai-crm/1.0",
            },
            timeout=30.0,
        )

    def _request(
        self,
        method: str,
        endpoint: str,
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Make an API request to trycompai.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path (e.g., "/v1/search_crm")
            data: Request body data
            params: Query parameters

        Returns:
            Parsed JSON response

        Raises:
            TrycompaiCrmError: On API errors
        """
        try:
            if method == "GET":
                response = self.client.get(endpoint, params=params)
            elif method == "POST":
                response = self.client.post(endpoint, json=data)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise TrycompaiCrmError(f"API request failed: {e}") from e
        except json.JSONDecodeError as e:
            raise TrycompaiCrmError(f"Failed to parse API response: {e}") from e

    def search_crm(self, query: str, filter_type: str | None = None) -> dict:
        """Search contacts/companies in the CRM.

        Args:
            query: Search query string
            filter_type: Optional filter ('contact' or 'company')

        Returns:
            Search results with matches and scores
        """
        params = {"query": query}
        if filter_type:
            params["type"] = filter_type
        return self._request("GET", "/v1/search_crm", params=params)

    def identify_contact(
        self,
        email: str | None = None,
        phone: str | None = None,
        name: str | None = None,
    ) -> dict:
        """Match contact by email, phone, or name.

        Args:
            email: Email address to match
            phone: Phone number to match
            name: Name to match

        Returns:
            Contact identification result with confidence and evidence
        """
        if not any([email, phone, name]):
            raise TrycompaiCrmError(
                "At least one identifier (email, phone, name) required"
            )

        data = {}
        if email:
            data["email"] = email
        if phone:
            data["phone"] = phone
        if name:
            data["name"] = name

        return self._request("POST", "/v1/identify_contact", data=data)

    def record_fact(
        self, contact_id: str, key: str, value: str, evidence: str | None = None
    ) -> dict:
        """Record an observed fact about a contact.

        Critical: trycompai does NOT accept confidence scores. Only record
        observed facts with clear evidence.

        Args:
            contact_id: ID of the contact
            key: Field name (e.g., 'company_name', 'role', 'seniority')
            value: The observed value
            evidence: Where/how the value was observed (e.g., 'From email signature')

        Returns:
            Recorded fact with ID and timestamp
        """
        data = {
            "contact_id": contact_id,
            "key": key,
            "value": value,
        }
        if evidence:
            data["evidence"] = evidence

        return self._request("POST", "/v1/record_fact", data=data)

    def enrich_company(self, company_name: str, domain: str | None = None) -> dict:
        """Fetch enriched company metadata.

        Args:
            company_name: Company name
            domain: Optional company domain for more precise matching

        Returns:
            Company enrichment data (founding date, stage, headcount, industry, CEO, etc.)
        """
        data = {"company_name": company_name}
        if domain:
            data["domain"] = domain

        return self._request("POST", "/v1/enrich_company", data=data)

    def schedule_recheck(
        self, contact_id: str, reason: str, due_in_days: int | None = None
    ) -> dict:
        """Schedule a future follow-up task.

        Args:
            contact_id: ID of the contact
            reason: Reason for the recheck
            due_in_days: Days from now when the task should be due

        Returns:
            Scheduled task with ID and due date
        """
        data: dict[str, Any] = {
            "contact_id": contact_id,
            "reason": reason,
        }
        if due_in_days is not None:
            data["due_in_days"] = due_in_days

        return self._request("POST", "/v1/schedule_recheck", data=data)

    def read_crm_history(self, contact_id: str, limit: int | None = None) -> dict:
        """Read past interactions with a contact.

        Args:
            contact_id: ID of the contact
            limit: Maximum number of interactions to return

        Returns:
            List of interactions with timestamps and details
        """
        params = {}
        if limit is not None:
            params["limit"] = limit

        return self._request("GET", f"/v1/read_crm_history/{contact_id}", params=params)

    def close(self) -> None:
        """Close the HTTP client connection."""
        self.client.close()


def trycompai_crm_execute(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Execute a trycompai CRM operation block.

    Block content should be JSON with:
    - operation (str): search_crm, identify_contact, record_fact, enrich_company, schedule_recheck, read_crm_history
    - other fields: operation-specific parameters

    Example:
    ```trycompai_crm
    {
      "operation": "search_crm",
      "query": "alice",
      "filter_type": "contact"
    }
    ```
    """
    if code is None:
        yield Message("system", "**Error**: No code block provided")
        return

    try:
        op_args = json.loads(code)
    except json.JSONDecodeError as e:
        yield Message("system", f"**Error**: Invalid JSON in block: {e}")
        return

    if not isinstance(op_args, dict):
        yield Message("system", "**Error**: JSON block must contain an object")
        return

    operation = op_args.pop("operation", None)
    if not operation:
        yield Message(
            "system",
            "**Error**: 'operation' field is required. Supported: search_crm, identify_contact, record_fact, enrich_company, schedule_recheck, read_crm_history",
        )
        return

    client = None
    try:
        client = TrycompaiCrmClient()

        result = None
        if operation == "search_crm":
            result = client.search_crm(**op_args)
        elif operation == "identify_contact":
            result = client.identify_contact(**op_args)
        elif operation == "record_fact":
            result = client.record_fact(**op_args)
        elif operation == "enrich_company":
            result = client.enrich_company(**op_args)
        elif operation == "schedule_recheck":
            result = client.schedule_recheck(**op_args)
        elif operation == "read_crm_history":
            result = client.read_crm_history(**op_args)
        else:
            yield Message("system", f"**Error**: Unknown operation '{operation}'")
            return

        yield Message("system", f"```json\n{json.dumps(result, indent=2)}\n```")

    except TrycompaiCrmError as e:
        yield Message("system", f"**Error**: {e}")
    except TypeError as e:
        yield Message("system", f"**Error**: Invalid arguments for '{operation}': {e}")
    except Exception as e:
        logger.exception("Unexpected error in trycompai_crm_execute")
        yield Message("system", f"**Error**: Unexpected error: {e}")
    finally:
        if client is not None:
            client.close()


# Tool specification
tool = ToolSpec(
    name="trycompai_crm",
    desc="Orchestrate CRM workflows: search contacts, identify matches, enrich company data, record facts, schedule follow-ups.",
    instructions="""
Use when you need to look up, update, or schedule CRM actions during outreach or research. Requires TRYCOMPAI_API_KEY env var.

Block content must be a JSON object with an "operation" field and operation-specific args:
- search_crm: query (str), filter_type? ('contact'|'company')
- identify_contact: email?, phone?, name? — at least one required
- record_fact: contact_id, key, value, evidence? — only observed facts, no confidence scores
- enrich_company: company_name, domain?
- schedule_recheck: contact_id, reason, due_in_days?
- read_crm_history: contact_id, limit?

Prefer identify_contact before record_fact to confirm the right contact_id.
""".strip(),
    block_types=["trycompai_crm"],
    execute=trycompai_crm_execute,
    available=True,
)
