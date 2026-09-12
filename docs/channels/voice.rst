:audience: power-user

Voice
=====

`gptme-voice <https://github.com/gptme/gptme-contrib/tree/master/packages/gptme-voice>`__
gives an agent real-time voice conversations through the OpenAI or xAI (Grok)
Realtime APIs. It loads the agent's personality from its workspace and hands
workspace tasks to gptme subagents while the conversation continues.

Talk locally
------------

From a gptme-contrib checkout:

.. code-block:: bash

    cd packages/gptme-voice && poetry install -E local
    gptme-voice-server                   # auto-detects the agent workspace
    gptme-voice-server --provider grok   # use xAI instead of OpenAI
    gptme-voice-client                   # in another terminal: mic and speaker

API keys come from your gptme config (``OPENAI_API_KEY``, or ``XAI_API_KEY`` for
Grok). Use headphones: with speakers, the client mutes the microphone while the
agent talks, so you can't interrupt it.

Phone calls
-----------

With a Twilio number, the agent can take and place calls. Expose the server
publicly (for example ``gptme-voice-server --port 8080`` behind ``ngrok http 8080``),
then:

- **Incoming:** set the number's Voice webhook to ``https://<public-url>/incoming``.
- **Outgoing:** set ``TWILIO_ACCOUNT_SID``, ``TWILIO_AUTH_TOKEN``,
  ``TWILIO_PHONE_NUMBER``, and ``GPTME_VOICE_PUBLIC_BASE_URL``, then run
  ``gptme-voice-call +15555550100``. Add ``--dry-run`` to print the TwiML without
  dialing.

Tool calls start a full gptme subprocess, so their results take a few seconds and
are spoken when ready.
