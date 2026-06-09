# Choosing your model

gptme is model-agnostic: it works with any LLM through a single `--model` flag, and
you can pick a different model for every task. Use a small, fast model for quick
questions and a powerful reasoning model for complex code — without changing tools,
formats, or workflow.

This page is a task-oriented guide to *picking* a model. For the full provider
reference (credentials, OAuth onboarding, OpenAI-compatible servers), see
{doc}`providers`.

## Pick a model per session

Pass `--model` (`-m`) as `<provider>/<model>` to choose the model for a single run:

```sh
# Quick question — small, cheap, fast
gptme "what does this regex match?" -m openrouter/qwen/qwen3.7-max

# Complex coding — powerful reasoning model
gptme "refactor this module for testability" -m anthropic/claude-sonnet-4-6

# Use a provider default (no model specified)
gptme "hello" -m anthropic
```

List the models gptme knows about at any time:

```sh
gptme '/models' - '/exit'
```

The rule of thumb: **match the model to the job.** Triage, summarization, and
quick lookups run fine on small models; multi-step coding and reasoning benefit
from a frontier model. Picking per task keeps cost down without capping capability.

## One key, many models: OpenRouter

[OpenRouter](https://openrouter.ai/) is the easiest way to reach many models
without managing a separate API key for each provider. With one
`OPENROUTER_API_KEY` you can route to 100+ models from Anthropic, OpenAI, Google,
DeepSeek, xAI, and more:

```sh
gptme "hello" -m openrouter/anthropic/claude-sonnet-4-6
gptme "hello" -m openrouter/deepseek/deepseek-v4-pro
gptme "hello" -m openrouter/x-ai/grok-4
```

gptme applies privacy-first defaults for OpenRouter (data collection denied,
provider routing requires full parameter support). See the OpenRouter section of
the {doc}`providers` page for configuration details, quantization controls, and
provider pinning.

## Set a default model

If you mostly use one model, set it once in `gptme.toml` (project) or
`~/.config/gptme/config.toml` (global) instead of passing `--model` every time:

```toml
# gptme.toml or ~/.config/gptme/config.toml
model = "openrouter/qwen/qwen3.7-max"
```

With a default configured, `gptme "query"` uses that model, and `--model` still
overrides it per run when you need something stronger or cheaper.

See {doc}`config` for the full config reference.

## Per-agent models

When you run multiple agents — for example a team of agents each handling a
different role — each can have its own model. Set the model in the agent's own
config so a fast agent and a reasoning-heavy agent can coexist without per-call
flags:

```toml
# Each agent's gptme.toml
model = "anthropic/claude-sonnet-4-6"
```

This is how an agent "brain" pins its default model: configure it once in the
agent's config, override per session only when a specific task needs a different
model. No vendor lock-in, no format changes.

## See also

- {doc}`providers` — full provider reference: credentials, OAuth,
  OpenAI-compatible servers, local models
- {doc}`custom-providers` — add your own OpenAI-compatible provider
- {doc}`config` — `gptme.toml` and global config reference
- {doc}`evals` — how different models perform on gptme's benchmark suite
