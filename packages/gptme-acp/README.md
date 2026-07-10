# gptme-acp

ACP (Agent Client Protocol) entry point for [gptme](https://github.com/gptme/gptme).

This is a thin shim package that allows gptme to be listed in the [ACP agent registry](https://github.com/agentclientprotocol/registry) and launched via `uvx`, enabling agent discovery in editors like Zed and JetBrains.

## Usage

### Quick start (no install required)

```bash
uvx gptme-acp
```

### Persistent install

```bash
pipx install gptme-acp
gptme-acp
```

## What this package does

`gptme-acp` is a minimal wrapper that:

1. Declares `gptme[acp]` as its dependency (pulls in gptme + the `agent-client-protocol` library)
2. Exposes `gptme-acp` as its default executable — the ACP server that editors connect to

The agent communicates with editors via JSON-RPC over stdio using the ACP protocol.

## ACP Agent Registry

This package exists so gptme can be listed in the [ACP agent registry](https://github.com/agentclientprotocol/registry):

```json
{
  "package": "gptme-acp",
  "launch": {"type": "uvx"}
}
```

The registry powers agent discovery in Zed, JetBrains, and other ACP-compatible editors. Without this package, the registry cannot launch gptme because:

- `uvx gptme` runs the interactive CLI, not the ACP server
- `uvx --from 'gptme[acp]' gptme-acp` requires `--from`, a shape the registry cannot express
- Extras-qualified specs like `gptme[acp]` fail the registry's package-existence check

## Configuration

The ACP agent uses gptme's standard configuration (`~/.config/gptme/config.toml`). API keys are read from environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.).

## Documentation

Full ACP documentation: https://gptme.org/docs/acp.html
