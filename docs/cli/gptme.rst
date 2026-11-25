:audience: power-user

gptme CLI
=========

.. click:: gptme.cli.main:main
   :prog: gptme
   :nested: full

Bounded native-tool conversations
---------------------------------

For controlled OpenRouter trials, opt in to three bounds together:

.. code-block:: bash

   gptme --model openrouter/anthropic/claude-haiku-4.5 \
     --no-stream --tool-format tool --tools shell,save,patch \
     --max-input-bytes 65536 --max-provider-requests 8 --max-tokens 1024 \
     --non-interactive -- "Inspect the supplied fixture and write the answer."

The request allowance is shared across model replies, tool-result continuations,
failed sends and retries. Each request has a serialized JSON input limit
(including tool schemas and routing parameters) and an output-token ceiling.
SDK retries and HTTP redirects are disabled; the OpenRouter endpoint and exact
model are pinned, and routing cannot relax parameter enforcement. Exhaustion
stops generation. A new CLI invocation starts a new allowance.

This mode requires nonstreaming native tools, an explicit OpenRouter model, and
only ``shell``, ``save``, ``append``, ``patch``, ``read`` or ``complete`` tools. Attachments and
structured-output schemas are rejected; background conversation naming is
suppressed. Use a clean configuration without generation hooks or plugins.
These bounds cover gptme's provider requests, not arbitrary network requests
or child processes launched by tools. Sandbox and restrict tool execution
separately. They are not a currency budget or a guarantee of provider billing.

Library callers can use the same scope across sequential ``llm.reply`` calls:

.. code-block:: python

   from gptme import llm
   from gptme.llm.request_limits import request_limits

   with request_limits(
       model, 65536, 8, max_tokens=1024, native_tools=True
   ):
       answer = llm.reply(messages, model, tools=tools)
       # Append answer and tool results, then reply again inside this scope.

Do not pass per-reply input/request bounds inside the shared scope. An omitted
``max_tokens`` inherits its ceiling; an explicit smaller ceiling is allowed.
The existing text-only per-reply bounds retain their independent allowances.
