Timeline
========

A brief timeline of the project.

The idea is to later make this into a timeline similar to the one for `ActivityWatch <https://activitywatch.net/timeline/>`_, including releases, features, etc.

.. figure:: https://starchart.cc/gptme/gptme.svg
   :alt: Stargazers over time
   :target: https://starchart.cc/gptme/gptme

   GitHub stargazers over time

..
    This timeline tracks development across the entire gptme ecosystem, including:

    - `gptme <https://github.com/gptme/gptme>`_ (main repository)
    - `gptme-agent-template <https://github.com/gptme/gptme-agent-template>`_
    - `gptme-rag <https://github.com/gptme/gptme-rag>`_
    - `gptme.vim <https://github.com/gptme/gptme.vim>`_
    - `gptme-webui <https://github.com/gptme/gptme-webui>`_

    For repositories with formal releases, we track significant version releases.
    For repositories without formal releases (like gptme.vim and gptme-webui),
    we track initial releases and major feature additions based on commit history.

    This file can be automatically updated by gptme with the help of `gh release list` and `gh release view` commands.

2025
----

March

- v0.27.0 (2025-03-11)

  - Pre-commit integration for automatic code quality checks
  - macOS support for computer use tool
  - Claude 3.7 Sonnet and DeepSeek R1 support
  - Improved TTS with Kokoro 1.0
  - Context tree for including repository structure in prompts
  - Enhanced RAG with LLM post-processing

February

- Added image support to gptme-webui (2025-02-07)

January

- Major UI improvements to gptme-webui (2025-01-28)
- v0.26.0 (2025-01-14)

  - Added support for loading tools from external modules (custom tools)
  - Added experimental local TTS support using Kokoro

- gptme-contrib repository created (2025-01-10)

  - Initial tools: Twitter and Perplexity CLI integrations
  - Later expanded with Discord bot, Pushover notifications, and enhanced Twitter automation

2024
----

December

- v0.25.0 (2024-12-20)

  - New prompt_toolkit-based interface with better completion and highlighting
  - Support for OpenAI/Anthropic tools APIs
  - Improved cost & performance through better prompt caching
  - Better path handling and workspace context
  - Added heredoc support
- gptme-agent-template v0.3 release (2024-12-20)
- gptme-rag v0.5.1 release (2024-12-13)

November

- gptme.vim initial release (2024-11-29)
- v0.24.0 (2024-11-22)
- gptme-rag v0.3.0 release (2024-11-22)
- gptme-agent-template initial release v0.1 (2024-11-21)
- gptme-rag initial release v0.1.0 (2024-11-15)
- v0.23.0 (2024-11-14)
- gptme-webui initial release (2024-11-03)
- v0.22.0 (2024-11-01)

October

- v0.21.0 (2024-10-25)
- v0.20.0 (2024-10-10)

  - Updated web UI with sidebar
  - Improved performance with faster imports
  - Enhanced error handling for tools

- `First viral tweet <https://x.com/rohanpaul_ai/status/1841999030999470326>`_ (2024-10-04)
- v0.19.0 (2024-10-02)

September

- v0.18.0 (2024-09-26)
- v0.17.0 (2024-09-19)
- v0.16.0 (2024-09-16)
- v0.15.0 (2024-09-06)

  - Added screenshot_url function to browser tool
  - Added GitHub bot features for non-change questions/answers
  - Added special prompting for non-interactive mode

August

- v0.14.0 (2024-08-21)
- v0.13.0 (2024-08-09)

  - Added Anthropic Claude support
  - Added tmux terminal tool
  - Improved shell tool with better bash syntax support
  - Major tools refactoring

- v0.12.0 (2024-08-06)

  - Improved browsing with assistant-driven navigation
  - Added subagent tool (early version)
  - Tools refactoring

- `Show HN <https://news.ycombinator.com/item?id=41204256>`__

2023
----

November

- v0.11.0 (2023-11-29)

  - Added support for paths/URLs in prompts
  - Mirror working directory in shell and Python tools
  - Started evaluation suite

- v0.10.0 (2023-11-03)

  - Improved file handling in prompts
  - Added GitHub bot documentation

October

- v0.9.0 (2023-10-27)

  - Added automatic naming of conversations
  - Added patch tool
  - Initial documentation

- v0.8.0 (2023-10-16)

  - Added web UI for conversations
  - Added rename and fork commands
  - Improved web UI responsiveness

- v0.7.0 (2023-10-10)
- v0.6.0 (2023-10-10)
- v0.5.0 (2023-10-02)

  - Added browser tool (early version)

September

- v0.4.0 (2023-09-10)
- v0.3.0 (2023-09-06)

  - Added configuration system
  - Improved context awareness
  - Made OpenAI model configurable

- `Reddit announcement <https://www.reddit.com/r/LocalLLaMA/comments/16atlia/gptme_a_fancy_cli_to_interact_with_llms_gpt_or/>`_ (2023-09-05)
- `Twitter announcement <https://x.com/ErikBjare/status/1699097896451289115>`_ (2023-09-05)
- `Show HN <https://news.ycombinator.com/item?id=37394845>`__ (2023-09-05)
- v0.2.1 (2023-09-05)

  - Initial release

August

March

- `Initial commit <https://github.com/gptme/gptme/commit/d00e9aae68cbd6b89bbc474ed7721d08798f96dc>`_
