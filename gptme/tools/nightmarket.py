"""Nightmarket tool — API marketplace for AI agents.

Lets gptme agents discover and call paid third-party API services through
the Nightmarket marketplace (https://nightmarket.ai). Every call settles
on-chain in USDC on Base using the x402 payment protocol.
"""

import json
import logging
from collections.abc import Generator

from ..message import Message
from . import ToolSpec, ToolUse

logger = logging.getLogger(__name__)

NIGHTMARKET_BASE_URL = "https://nightmarket.ai/api"

instructions = """
## Nightmarket — API Marketplace

Use the `nightmarket` tool to discover and call paid third-party API services.

**Search for services:**
```nightmarket
search weather
```

**Get service details:**
```nightmarket
details abc123def456
```

**Call a service:**
```nightmarket
call abc123def456 GET
```

**Call with payment (after getting 402 and paying via CrowPay):**
```nightmarket
call abc123def456 POST {"query": "test"} payment_signature_base64_here
```

Every first call returns 402 Payment Required. Use CrowPay to handle payment,
then retry with the payment signature. See https://nightmarket.ai for more info.
"""

examples = """
User: Search for weather APIs on Nightmarket
Assistant: Let me search the marketplace.
```nightmarket
search weather
```

User: Get details for service abc123
Assistant:
```nightmarket
details abc123
```
"""


def _http_get(url: str) -> dict | list:
    from urllib.request import Request, urlopen

    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _http_request(url: str, method: str = "GET", body: str | None = None, headers: dict | None = None) -> tuple[int, str]:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    data = body.encode() if body else None
    if data:
        req_headers["Content-Type"] = "application/json"

    req = Request(url, data=data, headers=req_headers, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except HTTPError as e:
        return e.code, e.read().decode() if e.fp else "{}"


def execute_nightmarket(
    code: str, args: list[str], kwargs: dict, confirm: ToolUse.ConfirmFunc
) -> Generator[Message, None, None]:
    """Execute a nightmarket command."""
    lines = code.strip().split("\n")
    if not lines:
        yield Message("system", "Usage: search <query> | details <id> | call <id> <method> [body] [payment_sig]")
        return

    parts = lines[0].split(maxsplit=1)
    cmd = parts[0].lower()

    try:
        if cmd == "search":
            query = parts[1] if len(parts) > 1 else ""
            from urllib.parse import quote
            url = f"{NIGHTMARKET_BASE_URL}/marketplace"
            if query:
                url += f"?search={quote(query)}"
            result = _http_get(url)
            if isinstance(result, list):
                output = f"Found {len(result)} services:\n\n"
                for svc in result[:10]:
                    output += f"- **{svc.get('name', 'Unknown')}** (ID: `{svc.get('_id', '')}`) — ${svc.get('priceUsdc', '?')}/call\n  {svc.get('description', '')}\n"
                yield Message("system", output)
            else:
                yield Message("system", json.dumps(result, indent=2))

        elif cmd == "details":
            if len(parts) < 2:
                yield Message("system", "Usage: details <endpoint_id>")
                return
            endpoint_id = parts[1].strip()
            result = _http_get(f"{NIGHTMARKET_BASE_URL}/marketplace/{endpoint_id}")
            yield Message("system", json.dumps(result, indent=2))

        elif cmd == "call":
            call_parts = lines[0].split(maxsplit=4)
            if len(call_parts) < 3:
                yield Message("system", "Usage: call <endpoint_id> <method> [body_json] [payment_signature]")
                return
            endpoint_id = call_parts[1]
            method = call_parts[2].upper()
            body = call_parts[3] if len(call_parts) > 3 else None
            payment_sig = call_parts[4] if len(call_parts) > 4 else None

            headers = {}
            if payment_sig:
                headers["payment-signature"] = payment_sig

            url = f"{NIGHTMARKET_BASE_URL}/x402/{endpoint_id}"
            status, resp_body = _http_request(url, method, body, headers if headers else None)

            if status == 402:
                yield Message("system", f"**402 Payment Required**\n\nUse CrowPay to authorize this payment, then retry with the payment signature.\n\n```json\n{resp_body}\n```")
            else:
                yield Message("system", f"Response ({status}):\n```json\n{resp_body}\n```")

        else:
            yield Message("system", f"Unknown command: {cmd}. Use: search, details, or call")

    except Exception as e:
        yield Message("system", f"Nightmarket error: {e}")


tool: ToolSpec = ToolSpec(
    name="nightmarket",
    desc="Discover and call paid API services on the Nightmarket marketplace (https://nightmarket.ai). Uses x402 payment protocol with USDC on Base.",
    instructions=instructions,
    examples=examples,
    execute=execute_nightmarket,
    block_types=["nightmarket"],
)
