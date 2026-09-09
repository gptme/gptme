Finetuning
==========

A working recipe for fine-tuning a small open model on your own gptme
conversations, in gptme's own tool formats, and then measuring whether it
actually got better with `gptme-eval`.

Everything below was run end to end on 2026-09-08/09 against a real corpus of
agent sessions. The numbers, the config keys and the gotchas are measured, not
sketched. Where something was not measured, this page says so.

**What this is not.** It is not a cost-optimization guide — fine-tuning a 0.8B
model will not make gptme cheaper than a frontier API, and that is not why you
would do this. It is not a production pipeline either: there is no scheduler,
no data versioning, no retraining loop. It is a recipe you can follow once, in
an afternoon, for about $15 of rented GPU, to find out what your own
trajectories are worth as training data.

The single most useful result from the run: **one epoch of SFT on your own
sessions rendered in gptme's `markdown` format took a Qwen3.5-0.8B from 10% to
19% on gptme-eval, and the gain transferred to native tool calling (15% → 22%)**.
The full table, and the caveats it needs, are in *Results* below.

## Data

### Where the conversations are

gptme stores every conversation as `~/.local/share/gptme/logs/<name>/conversation.jsonl`,
one JSON object per line: `{role, content, timestamp}`. That is your corpus.
There is no separate export step and no telemetry — the logs are already on
disk.

### Two tool-call encodings, and why that matters

Tool calls appear in `conversation.jsonl` in **two different text encodings**,
and a parser that matches only one will silently drop whole sessions:

1. **Markdown fences** — the default `markdown` tool format. The assistant
   message contains a fenced block whose language tag is a tool name
   (`shell`, `ipython`, `patch <path>`, `save <path>`), and the result arrives
   as the *following* `system` message.
2. **The native form** — `@tool_name(call_id): {json}` at the start of a line,
   which is how gptme renders provider-side tool calls back into the log (see
   {doc}`tool-formats`).

Both live in the same corpus, often in the same directory tree. A previous
analysis pass that grepped only for fences reported 17 loop patterns across 50
sessions; adding the `@tool(id): {json}` branch took the same corpus to 341.
The "agents don't retry" conclusion drawn from the first number was a parser
artifact. Match both:

```python
from gptme.tools.base import find_json_end, toolcall_re

# fence form (markdown tool format):  ```shell\n<code>\n```
# native form:                        @shell(abc123): {"command": "ls"}
# toolcall_re only finds the START of a call. Its `{.*` capture is DOTALL and
# greedy, so group(3) is not JSON and finditer() swallows later parallel
# calls in the same message. Resume from find_json_end after each match.
# MCP tools are named server.tool (dots). Provider call IDs include
# hyphens/colons (call_abc, toolu_..., call-123). `\w+` drops those.

def iter_native_calls(content: str):
    search_from = 0
    while match := toolcall_re.search(content, search_from):
        json_start = match.start(3)
        json_end = find_json_end(content, json_start)
        if json_end is None:
            break
        yield match.group(1), match.group(2), content[json_start:json_end]
        search_from = json_end
```

If you also mine Claude Code trajectories, note that those are a third shape
entirely — structured `tool_use` / `tool_result` content blocks in
`~/.claude/projects/<slug>/*.jsonl` — and need their own parser, not a text
match.

### The export format

Target [Axolotl]'s `chat_template` dataset type. One JSON object per line:

```text
{"messages": [...], "tools": [...]}
```

- `messages` uses the OpenAI shape. Assistant tool calls go in
  `tool_calls[].function`, tool results are
  `{"role": "tool", "tool_call_id", "name", "content"}`.
- `tools` is the JSON-schema tool list, only needed for the native format.
- **`arguments` must be a JSON *string*, not an object.** If you nest a real
  object there, Hugging Face `datasets` fails to cast the column and the run
  dies during preprocessing, not during training — so you pay for the box
  first. This is the single most common export bug.

### The three renderers, and why paired datasets

gptme has three tool formats, and they are genuinely different token
distributions, not cosmetic variants. So the exporter should be able to render
the *same* sessions into each of them:

| Renderer | Assistant turn | Tool result |
| --- | --- | --- |
| `markdown` | fenced block with the tool name as the language tag | following `system` message with gptme's own framing (`Ran command: …` + a `stdout` block, `Saved to X (overwritten)`, ``Patch successfully applied to `X` ``) |
| `xml` | `<tool-use><name args="…">…</name></tool-use>` | same, as a `system` message |
| `tool` | OpenAI `tool_calls` with gptme's tool names and the schemas gptme itself sends (from `ToolSpec.parameters`) | `{"role": "tool", …}`, plus a `tools` column |

Emit all three **from the same sessions with the same train/holdout split**.
That is what makes a format comparison meaningful: a markdown-trained adapter
must not be judged on xml output, and without paired datasets you cannot tell a
format effect from a data effect.

### Masking, truncation, sequence length

- **Assistant-only loss.** Train on assistant turns only
  (`roles_to_train: ["assistant"]`, which is Axolotl's default). On this data
  shape roughly **9% of tokens end up trainable** — the system prompt and every
  tool result are masked. That is expected, not a bug in your masking.
- **The gptme system prompt is large**: **~7–8.5k tokens** for a normal tool
  allowlist (7.7k in the run below). It is a constant shared by every row, so
  do not count it against your per-conversation truncation budget — but *do*
  size `sequence_len` as **conversation budget + system prompt**. An 8k
  conversation budget wants `sequence_len: 16384`, not 8192, or every row is
  silently truncated to system prompt plus a couple of turns.
- **End every row on an assistant turn.** A row that ends on a tool result has
  no trainable final token and teaches the model to stop mid-call. In the run
  below this hit **1 in 8 rows** before it was caught.
- **Hold out by time, not at random.** Use later months as the holdout. A
  random split over agent sessions leaks: the same task, the same repo and
  often near-identical tool sequences appear on both sides, and the eval
  measures memorization.
- **Strip per-row `meta`.** Any bookkeeping block you attach per row (grades,
  session ids, model names) will eventually contain a column that is `null` in
  the first rows and a float later — HF `datasets` cannot cast that and the job
  fails at preprocess time. Keep the metadata in a separate sidecar file, or
  emit the training JSONL with metadata off.

### Redaction

Agent trajectories are full of secrets: API keys echoed by a `shell` call,
tokens in environment dumps, credentials in config files the agent `cat`ed.
Redact before the data leaves the machine, and treat these three findings as
load-bearing:

- **Scrub decoded leaf strings, never the serialized `arguments` blob.**
  Running a regex over the JSON string corrupts escaping and produces rows that
  no longer parse. Walk the structure, redact the leaves, re-serialize.
- **Allowlist your own workspace paths.** Blanket path redaction destroys the
  thing you are training on — the model needs to learn that files live at real
  paths. Redact everything *except* an explicit allowlist (e.g. `/home/you/`).
- **A secret scanner's "blocked" verdict is not fail-closed.** The `openai_key`
  rule false-positives on any filename containing `sk-`, so a fail-closed
  policy would have silently dropped good sessions. Review the findings; do not
  wire the scanner straight to a drop filter.

Validate after export: zero call/result pairing violations, zero non-string
`arguments`, zero residual secrets, zero holdout-month leakage. All four are
cheap to check and all four have been violated in practice.

### Tooling

gptme ships `scripts/train/collect.py`, the **legacy collector**: it reads the
same logs, filters generated-name and low-quality chats, strips leading system
prompts, and renders each conversation through a Hugging Face chat template to
`train.csv` / `train.jsonl` in the current working directory. It predates
gptme's tool formats and native tool calling, so it does not emit `tool_calls`,
does not emit a `tools` column, and cannot render the three formats. It is fine
for a plain chat-style SFT and wrong for anything tool-shaped.

The run below used a private exporter that implements *Data* above — it is
not in this repository. Reimplement from that section; `collect.py` is not a
substitute for tool-shaped SFT. The exporter that produced these numbers
reads gptme logs and Claude Code trajectories, joins session-level labels,
does the redaction pass, and writes paired `markdown`/`xml`/`tool` JSONL
from the same sessions with no per-row metadata (the HF-datasets problem
above).

## Training

### Why Axolotl

[Axolotl] (v0.18.0 at time of writing), on this data shape:

- **Axolotl** — native OpenAI-style `messages` with `tool_calls` plus a `tools`
  column, assistant-only loss by default, char-offset masking if you need it,
  and a cloud image that ships SSH and tmux for rented boxes. Best fit.
- **TRL** — a strong second, "less magic". `assistant_only_loss=True` needs
  `{% generation %}` markers in the chat template; TRL auto-patches Qwen3/3.5,
  Gemma 3, Llama 3, gpt-oss and others, but you hand-patch anything else.
- **LLaMA-Factory** — popular, but forces strict human/assistant alternation,
  which is lossy for agent trajectories full of tool turns.
- **torchtune** — wound down in 2025 and tombstoned in 2026. Do not start here.
  (Judge a repo by its default-branch commits; `pushed_at` lies.)
- **Unsloth** — the fastest single-GPU LoRA engine, and fine *underneath*
  either of the first two.

### The config that worked

Qwen3.5-0.8B, LoRA, one H100. The keys that matter, with the reasons:

```yaml
base_model: Qwen/Qwen3.5-0.8B
output_dir: /out             # adapter lands here; the merge step reads it
chat_template: qwen3_5
freeze_mm_modules: true      # Qwen3.5 is a unified VLM; freeze the vision tower

adapter: lora
lora_r: 16
lora_alpha: 32
lora_dropout: 0.0
lora_target_modules:         # all projections, listed explicitly:
  - q_proj                   # lora_target_linear: true would sweep the
  - k_proj                   # vision tower's linears in too
  - v_proj
  - o_proj
  - gate_proj
  - up_proj
  - down_proj

datasets:
  - path: /data/train.jsonl
    ds_type: json
    type: chat_template
    field_messages: messages
    field_tools: tools
    roles_to_train: ["assistant"]
    train_on_eos: turn
test_datasets:               # a real time-based holdout, not a random slice
  - path: /data/holdout.jsonl
    ds_type: json
    split: train
    type: chat_template
    field_messages: messages
    field_tools: tools
    roles_to_train: ["assistant"]
    train_on_eos: turn
val_set_size: 0              # required when test_datasets is set

sequence_len: 16384          # 8k conversation budget + ~8k gptme system prompt
sample_packing: true
eval_sample_packing: false
pad_to_sequence_len: true

bf16: true
attn_implementation: flash_attention_2
gradient_checkpointing: true

num_epochs: 1
micro_batch_size: 1          # REQUIRED with packing on Qwen3.5 (see below)
gradient_accumulation_steps: 16
learning_rate: 2e-4
lr_scheduler: cosine
warmup_ratio: 0.03
optimizer: adamw_torch_fused
```

The non-obvious ones, three of which cost real money to discover:

- **`micro_batch_size: 1` is mandatory** with `sample_packing: true` +
  `flash_attention_2` on Qwen3.5. Anything else dies with *"The batch size is
  expected to be 1 rather than N when using `cu_seqlens`"*. Packing already
  fills each 16k sequence; scale with gradient accumulation instead.
- **`freeze_mm_modules: true`** because Qwen3.5 loads as a multimodal model
  even for text-only training. Without it you also need
  `ddp_find_unused_parameters`.
- **Do not enable the LoRA kernels** (`lora_mlp_kernel` and friends). Their
  supported base list stops at gemma3; qwen3 and qwen3_5 are not on it.
- QLoRA is not worth it here — a 0.8–4B bf16 LoRA fits comfortably on one
  80 GB card.

### Measured numbers

`q08b-markdown-v5` — Qwen3.5-0.8B, LoRA r16, 3,080 sessions rendered in the
`markdown` format, ~33M tokens, 1 epoch, one H100 SXM on RunPod:

| Metric | Value |
| --- | --- |
| Training wall clock | **1,170 s** (19.5 min) |
| Cost, all-in incl. setup, eval, upload | **~$3** |
| Train loss | 0.70 |
| Held-out eval loss | **1.918 → 1.721** (ppl 6.81 → 5.59) |
| Trainable token fraction | ~9% |

The paired `xml` and `tool` adapters on the same 3,080 sessions took 609 s and
1,465 s and reached eval loss 1.656 and 1.722. **Do not compare loss across
formats** — the token mixes differ. `gptme-eval` is the comparison.

Public artifacts for `q08b-markdown-v5` (adapter weights plus the generated
model card that embeds the Axolotl config):
https://s3.bob.gptme.org/training/runs/q08b-markdown-v5/README.md

It took five paid attempts (~$5 of debugging) to get one clean run, and every
single failure was plumbing, not learning: a missing tmux in the training
image, the HF-datasets `meta` cast, the packing/micro-batch constraint above,
an archive format the object-store client could not unpack, and a wall-clock
cap that killed a run at step 33 of 71. Budget the first $5 as integration
testing.

### Model choice

| Model | Why / why not |
| --- | --- |
| **Qwen3.5-0.8B** | The smallest rung and what was tested here. Weak in absolute terms — useful because the *deltas* are cheap to measure. There is **no 1B in Qwen3.5**; 0.8B is the floor. |
| **Qwen3.5-2B** | Next rung up, same config. |
| **Qwen3.5-4B** | The reference pick. Apache-2.0, ungated, native `tools` in the chat template, 256k context, best agentic scores in the 24 GB class, bf16 LoRA around 10 GB, and it has a `-Base` twin. Unified VLM, so `freeze_mm_modules`. |
| **SmolLM3-3B** | The no-drama fallback: dense, text-only, and it ships native `{% generation %}` markers, so TRL's assistant-only loss works without patching the template. |
| **Qwen3-4B-Instruct-2507** | Dense text-only, and the well-trodden vLLM-colocate path if you go on to RL/GRPO. Also the escape hatch if the unified-VLM path fights you: swap the base and set `chat_template: qwen3`. |

## Serving and evaluating

### Serve with vLLM

Axolotl's `output_dir` with `adapter: lora` contains only
`adapter_config.json` + `adapter_model.safetensors`. **vLLM serves full
checkpoints**, so merge the adapter into the base first. Then:

```bash
vllm serve /path/to/merged-checkpoint \
    --served-model-name tool-format-experiment \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_xml \
    --structured-outputs-config.backend xgrammar \
    --default-chat-template-kwargs '{"enable_thinking": false}' \
    --chat-template /path/to/qwen3_5_midsystem.jinja \
    --max-model-len 32768 \
    --port 8000
```

- `--enable-auto-tool-choice` is **mandatory** for the `tool` format.
- `--tool-call-parser`: `qwen3_xml` for Qwen3.5 and Qwen3-Coder (XML-style
  `<function=…>` template), `hermes` for plain Qwen3 instruct/thinking. Verify
  against the checkpoint's own `tokenizer_config.json` rather than guessing
  from the name.
- `--default-chat-template-kwargs '{"enable_thinking": false}'`: Qwen3.5 thinks
  by default. In the first eval attempt every generation ended at
  `Assistant: Thinking...` — the per-task timeout expired inside the think
  block — and both arms scored 0/116. Turn thinking off at serve time (or
  raise the timeout a lot).
- `--chat-template` in the command above is the **2026-09-08/09 serve path**
  (see the next section). On gptme with
  [#3780](https://github.com/gptme/gptme/pull/3780), drop it and fold on
  the client instead.

### The mid-conversation system message gotcha

**Qwen3.5's stock chat template raises `System message must be at the
beginning`.** gptme injects context as non-leading `system` messages
(tool results go out as `tool` and are not the problem). On the
2026-09-08/09 run this burned two entire evals (580/580 requests failed,
both arms 0/116). It reads like a broken model or a timeout; only the
vLLM server log gives it away.

**Fixed in gptme as of [#3780](https://github.com/gptme/gptme/pull/3780)**
(merged 2026-09-09, closes
[#3779](https://github.com/gptme/gptme/issues/3779)). `_fold_mid_system` in
`gptme/llm/llm_openai.py` keeps the first system message and re-emits
every later system message without a `call_id` as a *separate user*
message wrapped in `<system>…</system>` — the same transform `_prep_o1`
already used for the o-series. It activates when the model id contains
`qwen3.5` / `qwen3_5`, or when `GPTME_FOLD_SYSTEM_MESSAGES=1`. Tool-result
messages (`system` + `call_id`) stay for `_handle_tools` to convert to
`tool`.

This recipe's `--served-model-name` values (`tool-format-experiment`,
`sft`) do **not** contain `qwen3.5`, so name inference will not fire.
On a gptme that includes #3780, either put `qwen3.5` in the served name
(`--served-model-name qwen3.5-sft`, then `gptme-eval --model
local/qwen3.5-sft@markdown`) or `export GPTME_FOLD_SYSTEM_MESSAGES=1` for
the eval process.

The 2026-09-08/09 numbers were collected **before** #3780, by serving
with a **copy of the chat template** that renders non-leading system
messages as ordinary ChatML system turns instead of raising. That is
also the shape the SFT data was rendered in, so training and those
eval numbers agree. The client-side fold is a different transform
(role becomes `user` with a `<system>` wrapper). To reproduce the
table below, keep the template copy; to eval a Qwen3.5 on current
gptme, use the fold.

The template patch, for the historical serve path. In the message loop,
replace the `raise_exception` branch with:

```jinja
{%- if message.role == "system" %}
    {%- if not loop.first %}
        {{- '<|im_start|>system\n' + content + '<|im_end|>' + '\n' }}
    {%- endif %}
{%- endif %}
```

Copy the checkpoint's own `chat_template` (from `tokenizer_config.json`)
and apply that replacement; the rest of the template stays stock.

### Run gptme-eval

gptme-eval reaches a self-hosted server through the `local` provider. The model
spec carries the tool format after an `@`, and it **wins over `--tool-format`**:

```bash
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=vllm          # required, ignored by a keyless vLLM
export EVAL_RESULTS_DIR=~/evals/my-run

gptme-eval --model local/tool-format-experiment@markdown \
    --parallel 8 --timeout 600 basic all-practical
```

- `basic` (18 tests) + `all-practical` (98) = **116 tests** on this repo as
  of 2026-09-09, which is the cheapest suite pair that still discriminates.
  Recount from `gptme.eval.suites` if you need the denominator to match a
  later revision. See {doc}`evals` for the full suite list.
- Repeat with `@xml` and `@tool` for the other formats. With no `@` suffix,
  gptme-eval runs the model once per format.
- `--timeout` is the generation budget per eval, and the default is far too
  tight for a small model doing 10–15 agent turns. 600 s is generous; 60 s
  produced a wall of zeros.
- gptme-eval **does not sandbox**: each test runs its `run` command with
  `/bin/bash -c` in the agent's workspace, inheriting the environment. The box
  therefore needs `bash`, `git`, coreutils, `python3`, and `pytest` + `mypy` +
  `requests` + `pytest-mock` *in the interpreter the tasks resolve*, plus a
  global git identity. Get any of that wrong and you get uniformly failing
  tests that read like a bad model. Install `gptme`, not `gptme[eval]` — that
  extra is for the external benchmark lanes and pulls gigabytes these suites
  never touch.
- Don't put `init_projects` (needs cargo), `browser`/`computer` (need
  Playwright + Chromium) or `subagent` (nested LLM calls) in the suite list
  unless you have provisioned for them.

### Grammar-constrained decoding (experimental)

The `tool` format gets constrained decoding for free — schemas go to the
server, the server constrains the arguments. `markdown` and `xml` get nothing;
they are parsed heuristically out of free text. A self-hosted vLLM can
constrain those too, given a context-free grammar for the fenced-block and
`<tool-use>` shapes.

gptme does **not** ship a grammar passthrough for `markdown`/`xml`. The
grammar columns in *Results* are from a local experiment that sent GBNF as
`structured_outputs.grammar` on self-hosted vLLM only — they are **not part
of this recipe**. Skip that arm unless you have your own wiring. If you do:

- Send GBNF-flavoured **EBNF** with a rule named `root`. vLLM hands the string
  to `xgrammar.Grammar.from_ebnf`; its Lark detection is a one-line heuristic
  that routes non-`::=` grammars through a lossy converter.
- `structured_outputs.grammar` needs vLLM ≥ 0.11.0. The legacy
  `guided_grammar` field was removed in 0.12.0 and is **silently ignored**
  since — so a stale client returns unconstrained text with no error. Probe it:
  constrain a request to the single literal `GRAMMAR_OK` and refuse to run if
  the answer differs.
- Pin `--structured-outputs-config.backend xgrammar` so an invalid grammar
  fails loudly instead of quietly falling back to another engine.

## Results

Base Qwen3.5-0.8B versus the **markdown-trained** adapter, 116 tasks
(`basic` + `all-practical`), thinking off, one A40 for ~4 h (~$2):

| format | base, free | base, grammar | SFT, free | SFT, grammar |
| --- | --- | --- | --- | --- |
| `markdown` | 12/116 (10%) | 13/116 (11%) | **22/116 (19%)** | 19/116 (16%) |
| `xml` | 9/116 (8%) | 12/116 (10%) | 5/116 (4%) | 4/116 (3%) |
| `tool` (native) | 17/116 (15%) | n/a | **26/116 (22%)** | n/a |

`tool` + grammar is `n/a` by construction: gptme skips the grammar for the
native format because the provider already constrains against the tool schemas,
so that cell would be a byte-for-byte rerun.

What this says, honestly:

- **Format SFT works.** markdown 10% → 19% from *one epoch* of your own
  sessions rendered in that format. That is the headline.
- **It transfers to native tool calling** (15% → 22%). The adapter learned
  *what to do with gptme's tools*, not just the fence syntax. That was not
  obvious in advance.
- **It hurts the untrained format.** xml went 8% → 4%: the model now
  confidently emits fences, and in `xml` mode gptme does not parse markdown
  code blocks at all, so those calls are silently not executed (a documented
  weakness — see {doc}`tool-formats`). **Format training is format-specific.**
  Train the format you intend to run.
- **Grammar is not a substitute for training.** It is a small help for the
  untrained model (+1 / +3 pp) and a small *harm* on the SFT model (−3 pp,
  within noise). Syntax was not the bottleneck; semantics were.
- **n = 116**, so the binomial standard error is 3–4 pp. **Treat ±4 pp as
  noise.** The markdown and tool gains clear that bar; nothing else in the
  table does.
- **A 0.8B is weak in absolute terms.** 19% is not a usable agent. The point of
  the smallest rung is that the *deltas* cost $2 to measure; scale the model
  once the pipeline works, not before.

## Compute

Rent bare GPUs by the hour. No managed fine-tuning API is needed for any of
this, and the whole experiment above — three adapters, several evals, five
failed plumbing attempts and one idle cluster — came to **about $16**.

| Job | Hardware | Rate | Wall | Cost |
| --- | --- | --- | --- | --- |
| LoRA SFT, 3k sessions / ~33M tokens | 1× H100 | ~$2.89/h | ~20 min | ~$3 |
| Full eval, 2 arms × 5 cells × 116 tests | 1× A40 | ~$0.49/h | ~3.5 h | ~$2 |

The eval is bound on **agent turns, not GPU throughput**, so do not pay for an
H100 to run it — an A40 costs a quarter as much and finishes at the same time.
If the budget tightens, cut tasks per cell before cutting cells: a missing cell
kills a hypothesis, fewer tasks just widens the confidence interval.

**A forgotten H100 costs about $70/day**, which is more than this entire
experiment. Orchestrate with [SkyPilot], whose autostop daemon runs *on the
remote cluster* and therefore fires even if your laptop dies — neither RunPod
nor Vast has a native idle timeout of its own:

```yaml
resources:
  autostop:
    idle_minutes: 15
    down: true          # terminate; `stop` keeps the disk and keeps billing
    wait_for: none      # the load-bearing part
  max_hourly_cost: 3.6  # refuses to provision anything pricier
```

`wait_for: none` is what turns `idle_minutes` into a **hard wall clock**. The
default is `jobs_and_ssh`, which resets the timer while a job runs *or an SSH
session is open* — so `sky launch --down -i 15` alone does **not** tear the box
down after your job, and an open terminal will keep an H100 alive indefinitely.
Set the cap explicitly, and note SkyPilot's own documented exception: *if errors
occur during provisioning or setup, the cluster is not torn down* — precisely
the case where a forgotten box costs money. Run `sky down -y` from an
`EXIT`/`INT`/`TERM` trap, verify with `sky status --refresh` (without
`--refresh` SkyPilot answers from a local cache and will happily report a
cluster down while the provider bills it), and back all of it with a **prepaid,
hard-capped account**. That last one is the only cap that survives a wedged
daemon or a bug in your own launcher.

## Reproduce

The training, merge, serve, and eval commands below are what ran.
**The exporter is not in this repository.** Reimplement it from *Data*
above. The patched Qwen3.5 chat template is also not in-tree: it is
only needed to reproduce the 2026-09-08/09 numbers, or if you are on
a gptme without [#3780](https://github.com/gptme/gptme/pull/3780). Make
it by copying the checkpoint's `chat_template` and applying the six-line
replacement in
*The mid-conversation system message gotcha*. On current gptme, skip
the template and fold on the client (`GPTME_FOLD_SYSTEM_MESSAGES=1`,
or put `qwen3.5` in `--served-model-name`). Nothing here needs a GPU
until step 3.

```bash
# 1. Export paired datasets yourself (see Data). Hold out the last two months;
#    render markdown/xml/tool from the same sessions and split; omit per-row
#    metadata so HF datasets can cast the columns. Write
#    ~/data/sft/run.markdown.train.jsonl (and the xml/tool twins).

# 2. Validate before renting anything: this is where a bad `arguments` column
#    or a null-then-float field fails, and it costs nothing on your own box.
python3 -c "
from datasets import load_dataset
d = load_dataset('json', data_files='$HOME/data/sft/run.markdown.train.jsonl')
print(d)"

# 3. Train (on the rented box; ~20 min on one H100 for ~33M tokens).
axolotl preprocess qwen3.5-0.8b-lora.yaml
axolotl train qwen3.5-0.8b-lora.yaml

# 4. Merge the adapter into the base (vLLM serves full checkpoints, and step 3
#    produces only an adapter), then serve. --chat-template is the 2026-09-08/09
#    path (stock Qwen3.5 400s every gptme request without it). On gptme with
#    #3780, drop --chat-template and export GPTME_FOLD_SYSTEM_MESSAGES=1 (this
#    served name does not contain qwen3.5, so name inference will not fire).
python3 -c "
from peft import AutoPeftModelForCausalLM
from transformers import AutoTokenizer
AutoPeftModelForCausalLM.from_pretrained('/out').merge_and_unload().save_pretrained('/merged')
AutoTokenizer.from_pretrained('Qwen/Qwen3.5-0.8B').save_pretrained('/merged')"
vllm serve /merged --served-model-name sft \
    --enable-auto-tool-choice --tool-call-parser qwen3_xml \
    --default-chat-template-kwargs '{"enable_thinking": false}' \
    --chat-template ./qwen3_5_midsystem.jinja --port 8000

# 5. Score it — and score the *base* model the same way, or you have one number
#    and no verdict.
OPENAI_BASE_URL=http://localhost:8000/v1 OPENAI_API_KEY=vllm \
  gptme-eval --model local/sft@markdown --parallel 8 --timeout 600 \
    basic all-practical
```

Step 5 is the whole point. A fine-tuned model's score on its own is not a
result; the base-versus-SFT diff is.

## See also

- {doc}`tool-formats` — what `markdown`, `xml` and `tool` actually do, and the
  xml-ignores-fences weakness that shows up in the results table
- {doc}`evals` — the eval suites, the leaderboard, and per-model best formats
- [Axolotl] — the training stack used here
- [SkyPilot] — burst GPU orchestration with a remote-side autostop
- `scripts/train/collect.py` in this repo — the legacy chat-only collector

[axolotl]: https://docs.axolotl.ai/
[skypilot]: https://docs.skypilot.co/
