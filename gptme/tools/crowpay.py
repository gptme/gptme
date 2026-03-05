"""CrowPay tool — payment service for AI agents.

Gives gptme agents a wallet to pay for APIs and services autonomously,
within spending rules set by the wallet owner.
https://crowpay.ai
"""

import json
import logging
import os
from collections.abc import Generator

from ..message import Message
from . import ToolSpec

logger = logging.getLogger(__name__)

CROWPAY_BASE_URL = "https://api.crowpay.ai"

instructions = """
## CrowPay — Agent Payments

Use the `crowpay` tool to pay for APIs and services.

**Set up a wallet:**
```crowpay
setup
```

**Authorize an x402 payment (paste the 402 response body):**
```crowpay
authorize ServiceName "Reason for payment"
{"x402Version": 2, "resource": {...}, "accepts": [...]}
```

**Authorize a credit card payment:**
```crowpay
card 1000 "OpenAI" "GPT-4 API credits"
```

**Poll for approval status:**
```crowpay
status approval_id_here
```

**Report settlement:**
```crowpay
settle transaction_id tx_hash
```

Set your API key via the CROWPAY_API_KEY environment variable, or pass it as the first argument.
Default spending rules: auto-approve under $5, human approval above, $50/day limit.
"""

examples = """
User: Set up a CrowPay wallet for my agent
Assistant:
```crowpay
setup
```

User: Pay for that API call that returned 402
Assistant: Let me authorize the payment via CrowPay.
```crowpay
authorize "Weather API" "Fetching forecast for user"
{"x402Version": 2, "resource": {"url": "https://nightmarket.ai/api/x402/abc123"}, "accepts": [{"scheme": "exact", "network": "eip155:8453", "amount": "10000", "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "payTo": "0xSeller", "maxTimeoutSeconds": 60}]}
```
"""


def _get_api_key(explicit_key: str | None = None) -> str | None:
    """Get API key from explicit argument or environment variable."""
    if explicit_key and explicit_key.startswith("crow_sk_"):
        return explicit_key
    return os.environ.get("CROWPAY_API_KEY")


def _http_post(url: str, body: dict, api_key: str | None = None) -> tuple[int, dict]:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    data = json.dumps(body).encode()
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except HTTPError as e:
        body_str = e.read().decode() if e.fp else "{}"
        try:
            return e.code, json.loads(body_str)
        except json.JSONDecodeError:
            return e.code, {"error": body_str}


def _http_get(url: str, api_key: str) -> tuple[int, dict]:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    req = Request(url, headers={"Accept": "application/json", "X-API-Key": api_key})
    try:
        with urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except HTTPError as e:
        body_str = e.read().decode() if e.fp else "{}"
        try:
            return e.code, json.loads(body_str)
        except json.JSONDecodeError:
            return e.code, {"error": body_str}


def execute_crowpay(
    code: str | None, args: list[str] | None, kwargs: dict | None
) -> Generator[Message, None, None]:
    """Execute a crowpay command."""
    if not code:
        yield Message("system", "Usage: setup | authorize <merchant> <reason>\\n<402body> | card <cents> <merchant> <reason> | status <id> | settle <txn_id> <tx_hash>")
        return

    lines = code.strip().split("\n")
    if not lines:
        yield Message("system", "Usage: setup | authorize <merchant> <reason>\\n<402body> | card <cents> <merchant> <reason> | status <id> | settle <txn_id> <tx_hash>")
        return

    parts = lines[0].split(maxsplit=4)
    cmd = parts[0].lower()

    try:
        if cmd == "setup":
            status, result = _http_post(f"{CROWPAY_BASE_URL}/setup", {})
            if "apiKey" in result:
                yield Message("system", f"✅ Wallet created!\n\n- **API Key:** `{result['apiKey']}` (save this — shown only once!)\n- **Wallet:** `{result.get('walletAddress', '')}`\n- **Claim URL:** {result.get('claimUrl', '')}\n\nVisit the claim URL to set spending rules. Fund the wallet with USDC on Base.\n\n**Tip:** Set `CROWPAY_API_KEY` environment variable to avoid passing the key in commands.")
            else:
                yield Message("system", f"Setup response ({status}):\n```json\n{json.dumps(result, indent=2)}\n```")

        elif cmd == "authorize":
            # Parse: authorize [api_key] <merchant> <reason>\n<402_body>
            remaining = parts[1:]
            api_key = _get_api_key(remaining[0] if remaining else None)
            if api_key and remaining and remaining[0].startswith("crow_sk_"):
                remaining = remaining[1:]
            if len(remaining) < 2:
                yield Message("system", "Usage: authorize [api_key] <merchant> <reason>\\n<402_response_body_json>")
                return
            if not api_key:
                yield Message("system", "No API key found. Set CROWPAY_API_KEY env var or pass as first argument.")
                return
            merchant = remaining[0].strip('"')
            reason = remaining[1].strip('"')
            payment_body_str = "\n".join(lines[1:])
            payment_required = json.loads(payment_body_str)

            body = {"paymentRequired": payment_required, "merchant": merchant, "reason": reason, "platform": "gptme"}
            status, result = _http_post(f"{CROWPAY_BASE_URL}/authorize", body, api_key)

            if status == 200:
                import base64
                sig = base64.b64encode(json.dumps(result).encode()).decode()
                yield Message("system", f"✅ Payment approved! Retry your request with this header:\n\n`payment-signature: {sig}`")
            elif status == 202:
                approval_id = result.get('approvalId', '')
                yield Message("system", f"⏳ Needs human approval. Poll with:\n```crowpay\nstatus {approval_id}\n```")
            else:
                yield Message("system", f"❌ Payment response ({status}):\n```json\n{json.dumps(result, indent=2)}\n```")

        elif cmd == "card":
            # Parse: card [api_key] <amount_cents> <merchant> <reason>
            remaining = parts[1:]
            api_key = _get_api_key(remaining[0] if remaining else None)
            if api_key and remaining and remaining[0].startswith("crow_sk_"):
                remaining = remaining[1:]
            if len(remaining) < 3:
                yield Message("system", 'Usage: card [api_key] <amount_cents> <merchant> <reason>')
                return
            if not api_key:
                yield Message("system", "No API key found. Set CROWPAY_API_KEY env var or pass as first argument.")
                return
            amount = int(remaining[0])
            merchant = remaining[1].strip('"')
            reason = remaining[2].strip('"')

            body = {"amountCents": amount, "merchant": merchant, "reason": reason, "platform": "gptme"}
            status, result = _http_post(f"{CROWPAY_BASE_URL}/authorize/card", body, api_key)

            if status == 200:
                yield Message("system", f"✅ Card payment approved! SPT Token: `{result.get('sptToken', '')}`")
            elif status == 202:
                approval_id = result.get('approvalId', '')
                yield Message("system", f"⏳ Needs human approval. Poll with:\n```crowpay\nstatus {approval_id}\n```")
            else:
                yield Message("system", f"❌ Card payment response ({status}):\n```json\n{json.dumps(result, indent=2)}\n```")

        elif cmd == "status":
            # Parse: status [api_key] <approval_id>
            remaining = parts[1:]
            api_key = _get_api_key(remaining[0] if remaining else None)
            if api_key and remaining and remaining[0].startswith("crow_sk_"):
                remaining = remaining[1:]
            if not remaining:
                yield Message("system", "Usage: status [api_key] <approval_id>")
                return
            if not api_key:
                yield Message("system", "No API key found. Set CROWPAY_API_KEY env var or pass as first argument.")
                return
            from urllib.parse import urlencode
            approval_id = remaining[0]
            url = f"{CROWPAY_BASE_URL}/authorize/status?" + urlencode({"id": approval_id})
            status, result = _http_get(url, api_key)
            yield Message("system", f"Approval status:\n```json\n{json.dumps(result, indent=2)}\n```")

        elif cmd == "settle":
            # Parse: settle [api_key] <transaction_id> <tx_hash>
            remaining = parts[1:]
            api_key = _get_api_key(remaining[0] if remaining else None)
            if api_key and remaining and remaining[0].startswith("crow_sk_"):
                remaining = remaining[1:]
            if len(remaining) < 2:
                yield Message("system", "Usage: settle [api_key] <transaction_id> <tx_hash>")
                return
            if not api_key:
                yield Message("system", "No API key found. Set CROWPAY_API_KEY env var or pass as first argument.")
                return
            txn_id = remaining[0]
            tx_hash = remaining[1]
            status, result = _http_post(f"{CROWPAY_BASE_URL}/settle", {"transactionId": txn_id, "txHash": tx_hash}, api_key)
            yield Message("system", f"Settlement ({status}):\n```json\n{json.dumps(result, indent=2)}\n```")

        else:
            yield Message("system", f"Unknown command: {cmd}. Use: setup, authorize, card, status, settle")

    except (json.JSONDecodeError, ValueError) as e:
        yield Message("system", f"CrowPay error: {e}")
    except Exception:
        logger.exception("Unexpected error in CrowPay tool")
        raise


tool: ToolSpec = ToolSpec(
    name="crowpay",
    desc="Agent payment service — pay for APIs and services with USDC (x402) or credit card. https://crowpay.ai",
    instructions=instructions,
    examples=examples,
    execute=execute_crowpay,
    block_types=["crowpay"],
    disabled_by_default=True,
)
