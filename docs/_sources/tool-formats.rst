Tool Formats
============

gptme can hand tools to a model in three different ways, selected with
``--tool-format``:

- ``markdown`` — fenced code blocks (the default)
- ``xml`` — XML-tagged blocks
- ``tool`` — the provider's native tool/function-calling API

This page explains what each one does and how to pick one. For the architectural
rationale behind the non-native formats, see the "Tool Interface Architecture"
section of :doc:`tools` and the :doc:`design/ptc-tool-interface` design document.

Why there is more than one
--------------------------

gptme predates native tool/function calling being widely available and reliable,
so its original — and still default — interface is **Programmatic Tool Calling
(PTC)**: the model writes a fenced code block, and gptme executes it. No JSON
schemas are sent to the model, and no JSON-structured response is parsed.

Native tool calling later became available on most providers, and gptme supports
it as the ``tool`` format. It is not automatically the best choice. Native tool
calling gives you provider-side routing, constrained decoding against a schema,
and (on some providers) parallel tool calls. In exchange, every tool definition
is serialized into the request as JSON schema, and tool results accumulate as
structured blocks rather than plain text.

The practical upshot: **a good model should work reasonably well in all three
formats**, and which one wins is an empirical question per model. The
:doc:`evals` leaderboard reports the best-performing format per model from actual
eval runs — that table is the most reliable answer for any model listed there.

The three formats
-----------------

``markdown`` (default)
^^^^^^^^^^^^^^^^^^^^^^

The model emits a fenced code block whose language tag is a tool name:

.. code-block:: text

    ```patch test.txt
    <<<<<<< ORIGINAL
    old
    =======
    new
    >>>>>>> UPDATED
    ```

**Strengths.** Nothing is sent as JSON schema, so tool definitions cost little
context and are described in prose the model was heavily post-trained on. Writing
code in fenced blocks is the single most common thing in any code model's
training data, so instruction adherence tends to be high even for smaller models.
It degrades gracefully: a slightly malformed block is still often recoverable.

**Weaknesses.** Parsing is heuristic rather than schema-validated. Nested fenced
code blocks are the classic failure mode — gptme handles variable-length fences
(three or more backticks) and matches closing fences by length, but a
model that emits mismatched fences can truncate its own tool call. There is no
constrained decoding to stop the model inventing an argument.

``xml``
^^^^^^^

The same calls, wrapped in XML tags:

.. code-block:: xml

    <tool-use>
    <patch args="test.txt">
    ...
    </patch>
    </tool-use>

**Strengths.** Explicit delimiters make the boundary of a tool call unambiguous,
which sidesteps the nested-code-block problem entirely. Models that were
post-trained heavily on XML-tagged tool use (notably some Anthropic models) often
adhere better here than in markdown. The system prompt is also emitted
XML-sectioned in this mode.

**Weaknesses.** More verbose, and content must be XML-escaped. Crucially,
**in ``xml`` mode gptme does not parse markdown code blocks at all** — if the
model falls back to a fenced block, the call is silently not executed. That makes
``xml`` a poor fit for models that are inconsistent about which shape they emit.

``tool`` (native)
^^^^^^^^^^^^^^^^^

Tools are sent to the provider as JSON-schema function definitions, and the
provider returns structured tool calls, which gptme renders back into the log as:

.. code-block:: text

    @patch(call_abc123): {
      "path": "test.txt",
      "patch": "..."
    }

**Strengths.** Constrained decoding means arguments validate against the schema,
so malformed calls are largely eliminated. Providers that support **parallel tool
calls** can emit several calls in one response. On OpenAI Chat Completions, models
that support **strict tool schemas** (structured outputs) get an additional
guarantee that arguments conform exactly — gptme enables this only when every
parameter of the tool is required.

**Weaknesses.** Every enabled tool's schema is serialized into every request,
which costs context and grows with your tool allowlist. Tool descriptions are
truncated at 1024 characters (an OpenAI limit), so long tool instructions have to
be shortened for this format. Accuracy is also more sensitive to context rot in
long sessions — see the benchmark cited in :doc:`tools`.

Note that ``tool`` mode still parses markdown code blocks in addition to native
tool calls, so ``/impersonate`` and hand-written blocks keep working.

**Not every provider supports it.** On the OpenAI-compatible chat-completions
path, native tools are only accepted for ``openai``, ``azure``, ``openrouter``,
``deepseek``, ``moonshot``, ``local``, and custom providers. Selecting
``--tool-format tool`` on e.g. ``gemini``, ``xai``, ``groq``, ``nvidia`` or
``requesty`` raises an error rather than silently degrading. Anthropic, the
OpenAI Responses API, and ``openai-subscription`` all support it.

Choosing a format
-----------------

Start with the :doc:`evals` leaderboard: it lists the best-performing format per
model as measured, and that beats any rule of thumb.

Absent eval data for your model, these heuristics hold up:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Situation
     - Suggested format
   * - Frontier model, long autonomous sessions
     - ``markdown`` — best resilience to context rot, cheapest in context
   * - Model that reliably emits XML tool use, or markdown fences keep breaking
     - ``xml``
   * - You want parallel tool calls or schema-validated arguments
     - ``tool`` (check the model supports it — see below)
   * - Small / weakly instruction-tuned local model
     - Try ``xml`` first, then ``markdown``; ``tool`` usually needs a 7B+
       instruction-tuned model
   * - Model ignores tools or hallucinates arguments
     - ``tool`` if the provider supports it, to get constrained decoding

The considerations that actually differentiate models here are: support for
parallel tool calling, support for tool-calling schemas and constrained decoding,
and how much tool-use post-training the model received (which drives instruction
adherence). A model can be excellent in general and still be limited by any one
of these.

Setting the format
------------------

.. code-block:: sh

    gptme --tool-format xml "hello"

Or via environment variable / config:

.. code-block:: sh

    export GPTME_TOOL_FORMAT=xml

.. code-block:: toml

    # ~/.config/gptme/config.toml
    [env]
    TOOL_FORMAT = "xml"

Resolution order, highest priority first:

1. The ``--tool-format`` CLI flag.

2. The ``tool_format`` saved in a resumed conversation's chat config (it is
   sticky across ``--resume``).

3. ``GPTME_TOOL_FORMAT`` / ``TOOL_FORMAT``, from the process environment or an
   ``[env]`` section in chat, project, or user config (in that order).

4. The model's ``default_tool_format`` from its metadata, if set.

5. ``markdown``.

Switching model at runtime with ``/model`` also switches to that model's
``default_tool_format`` if it declares one.

.. _tool-format-model-metadata:

Model metadata
--------------

``ModelMeta`` (``gptme/llm/models/types.py``) carries per-model fields that
describe tool-calling capability. They are worth knowing about because they change
behaviour silently:

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Field
     - Meaning
   * - ``default_tool_format``
     - Preferred tool format for this model, used as a fallback when nothing
       higher-priority sets one (step 4 above).
   * - ``supports_parallel_tool_calls``
     - The model can emit multiple tool calls in a single response. When this is
       ``False``, gptme stops generation as soon as one complete tool call is
       seen, so at most one call per assistant message runs. Override with
       ``GPTME_BREAK_ON_TOOLUSE``.
   * - ``supports_strict_tools``
     - The model supports ``strict=True`` tool schemas (OpenAI structured
       outputs). Only used on the OpenAI chat-completions path, and only for tools
       whose parameters are all required.
   * - ``preferred_edit_format``
     - Hint for code edits — ``"diff"`` (patch/morph) or ``"whole"`` (save).
       Derived from Aider's empirical per-model edit-format registry. Not a tool
       format; listed here because it is adjacent and equally undocumented.

**How well populated is this?** Unevenly, and it is worth being explicit about:

- ``default_tool_format`` is set for the ``openai-subscription`` models only
  (they default to ``tool``). No other model in the bundled registry declares
  one, so for almost every model step 4 above is a no-op and you land on
  ``markdown``.

- ``supports_parallel_tool_calls`` is set on the Claude Opus/Sonnet 4.x families,
  their OpenRouter aliases, Kimi K3, and the GPT-4.1/GPT-5 families — roughly 30
  entries. Deliberate omissions are meaningful: Claude Haiku 4.5 carries an
  explicit comment that it does *not* emit multiple tool calls per response.

- ``supports_strict_tools`` is set on the OpenAI models (GPT-4o/4.1/5 families,
  o-series) and Kimi K3.

So this is not a complete capability matrix, and an absent flag means "not
recorded", not "not supported". If a model you use is missing a flag it should
have, that is a worthwhile contribution.

The :doc:`evals` leaderboard is the natural source for populating
``default_tool_format``: it already reports the best-performing format per model
from measured runs.

Troubleshooting
---------------

**"Provider doesn't support tools API"** — you selected ``--tool-format tool`` on
a provider that is not on the native-tools allowlist above. Use ``markdown`` or
``xml``.

**The model narrates a tool call but nothing runs** — in ``xml`` mode, markdown
code blocks are not parsed. Either switch to ``markdown`` or steer the model back
to XML.

**Tool calls truncate mid-content** — usually nested fenced code blocks in
``markdown`` mode. Try ``xml``.

**Only one tool runs per turn** — expected unless the model declares
``supports_parallel_tool_calls``. Set ``GPTME_BREAK_ON_TOOLUSE=false`` to
override, at the risk of the model emitting calls the provider will not accept.

**A small local model ignores tools entirely** — see the troubleshooting section
in :doc:`providers-custom`; small models often need ``xml`` and a 7B+
instruction-tuned base.
