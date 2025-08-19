# v0.28.0

These are the release notes for gptme version v0.28.0.

## Contributors

Thanks to everyone who contributed to this release:

@0xbrayo, @erikbjare, @Miyou, @RickardCarlsson4

## Changelog

Changes since v0.27.0:

## Summary

This is a **major feature release** packed with exciting improvements! 🎉

**🚀 Major New Capabilities:**
- **GPT-5 Support** - Works with OpenAI's latest model
- **Claude 4 Support** - Works with Anthropic's latest model
- **Bell and Tool Sounds** 🔊 - Pleasant notification sounds for user input requested and different operations (enable via `/setup` or `GPTME_DING`/`GPTME_TOOL_SOUNDS` env vars)
- **Perplexity Search** 🔍 - Enhanced web search capabilities through the browser tool
- **MCP Integration** 🔌 - Support for Model Context Protocol servers, expanding tool ecosystem

**🤖 Agent & Automation Enhancements:**
- **Setup Command** ⚙️ - Easy configuration with `/setup`
- **Auto-commit** - Automatically tell agent to git commit, or do it manually with `/commit`
- **Agent Workspace Auto-detection** - Smarter handling of agent environments
- **Choice Tool** - Interactive decision-making capabilities

**🛠 Developer Experience:**
- **Shell Timeouts** ⏱️ - Configurable command timeouts (set `GPTME_SHELL_TIMEOUT`)
- **Morph Tool** ✨ - Alternative fast patching tool for precise code edits
- **Computer Tool Improvements** 💻 - Better key sequence chaining for GUI automation
- **Enhanced TTS** 🗣️ - Improved text-to-speech with Chatterbox support

**🌐 Server & API:**
- **Redesigned Server API** - Complete API overhaul for better web UI integration
- **Tasks API** - New task management capabilities
- **OpenAPI Specification** - Comprehensive API documentation

**🔧 Quality of Life:**
- **90+ Bug Fixes** - Significantly improved stability and reliability
- **Better Configuration** - Enhanced config system with user-, project-, and chat-specific settings
- **Improved Error Handling** - More user-friendly error messages and recovery

## 📦 gptme

### ✨ Features (39)

 - feat: enhance telemetry with Prometheus metrics and comprehensive instrumentation ([#592](https://github.com/gptme/gptme/issues/592)) ([`a73f60b0`](https://github.com/gptme/gptme/commit/a73f60b0))
 - feat: improve eval system and fix tool format content extraction ([#618](https://github.com/gptme/gptme/issues/618)) ([`099c80b4`](https://github.com/gptme/gptme/commit/099c80b4))
 - feat: add GPT-5 support ([`4ff6d5c9`](https://github.com/gptme/gptme/commit/4ff6d5c9))
 - feat: add timeout support to shell tool ([`06d85f94`](https://github.com/gptme/gptme/commit/06d85f94))
 - feat: add last/all option to /replay command ([`ec4bec77`](https://github.com/gptme/gptme/commit/ec4bec77))
 - feat: added /api/v2/models endpoint ([#612](https://github.com/gptme/gptme/issues/612)) ([`b08f64e0`](https://github.com/gptme/gptme/commit/b08f64e0))
 - feat: add tool sounds for different operations ([#597](https://github.com/gptme/gptme/issues/597)) ([`ce6f50a2`](https://github.com/gptme/gptme/commit/ce6f50a2))
 - feat: add Perplexity search support to browser tool ([#566](https://github.com/gptme/gptme/issues/566)) ([`c91c9d18`](https://github.com/gptme/gptme/commit/c91c9d18))
 - feat: implemented choice tool ([#598](https://github.com/gptme/gptme/issues/598)) ([`07b602ee`](https://github.com/gptme/gptme/commit/07b602ee))
 - feat: add /setup command for user and project configuration ([#596](https://github.com/gptme/gptme/issues/596)) ([`2379e665`](https://github.com/gptme/gptme/commit/2379e665))
 - feat: add ding sound notification with bell audio ([#588](https://github.com/gptme/gptme/issues/588)) ([`6096474b`](https://github.com/gptme/gptme/commit/6096474b))
 - feat: add OpenTelemetry integration for performance monitoring ([#587](https://github.com/gptme/gptme/issues/587)) ([`41ab4283`](https://github.com/gptme/gptme/commit/41ab4283))
 - feat: add support for custom agent names in CLI/TUI interface ([`658fd3d6`](https://github.com/gptme/gptme/commit/658fd3d6))
 - feat: auto-detect agent workspaces ([`420d3072`](https://github.com/gptme/gptme/commit/420d3072))
 - feat: added support for --agent-path in CLI, include agent context in addition to workspace context ([`c40477e0`](https://github.com/gptme/gptme/commit/c40477e0))
 - feat: implement auto-commit ([#441](https://github.com/gptme/gptme/issues/441)) ([`cf0ed1c5`](https://github.com/gptme/gptme/commit/cf0ed1c5))
 - feat: support the openrouter /v1/models api to list all models with metadata ([#575](https://github.com/gptme/gptme/issues/575)) ([`88df6238`](https://github.com/gptme/gptme/commit/88df6238))
 - feat: added alternate patch tool using morph fast apply v2 via openro… ([#574](https://github.com/gptme/gptme/issues/574)) ([`23258e0b`](https://github.com/gptme/gptme/commit/23258e0b))
 - feat: add support for a GPTME_REASONING flag to explicitly enable/disable reasoning ([#568](https://github.com/gptme/gptme/issues/568)) ([`df0e0ca7`](https://github.com/gptme/gptme/commit/df0e0ca7))
 - feat: add automatic inclusion of default project files ([#565](https://github.com/gptme/gptme/issues/565)) ([`ac05da57`](https://github.com/gptme/gptme/commit/ac05da57))
 - feat: add chatterbox tts support ([#541](https://github.com/gptme/gptme/issues/541)) ([`84151f97`](https://github.com/gptme/gptme/commit/84151f97))
 - feat: openapi spec for server ([#563](https://github.com/gptme/gptme/issues/563)) ([`62bd55d6`](https://github.com/gptme/gptme/commit/62bd55d6))
 - feat: add tasks API for new tasks UI in gptme-webui ([#562](https://github.com/gptme/gptme/issues/562)) ([`6807d484`](https://github.com/gptme/gptme/commit/6807d484))
 - feat: added 'worked for `<time>`' output behind feature flag (due to issues) ([`ce78dda1`](https://github.com/gptme/gptme/commit/ce78dda1))
 - feat: added read_logs function to browser tool (playwright backend) ([#553](https://github.com/gptme/gptme/issues/553)) ([`9291e000`](https://github.com/gptme/gptme/commit/9291e000))
 - feat: add gptme-util models list/info commands with fish completion ([`82c551f7`](https://github.com/gptme/gptme/commit/82c551f7))
 - feat: add support for setting chat name in chat config ([#549](https://github.com/gptme/gptme/issues/549)) ([`5c30f5df`](https://github.com/gptme/gptme/commit/5c30f5df))
 - feat: added `gptme-util llm generate` command, rework `gptme-util context` ([#535](https://github.com/gptme/gptme/issues/535)) ([`87832050`](https://github.com/gptme/gptme/commit/87832050))
 - feat: added basic workspace file browsing api ([#530](https://github.com/gptme/gptme/issues/530)) ([`0ab8abf9`](https://github.com/gptme/gptme/commit/0ab8abf9))
 - feat: set enable_suspend (Ctrl+Z) and enable_open_in_editor (Ctrl-X Ctrl-E) for prompt (fixes [#525](https://github.com/gptme/gptme/issues/525)) ([#526](https://github.com/gptme/gptme/issues/526)) ([`ef33c190`](https://github.com/gptme/gptme/commit/ef33c190))
 - feat: add api_conversation_delete ([#519](https://github.com/gptme/gptme/issues/519)) ([`4fb3561b`](https://github.com/gptme/gptme/commit/4fb3561b))
 - feat: support ChatConfig in server and CLI ([#504](https://github.com/gptme/gptme/issues/504)) ([`0266e350`](https://github.com/gptme/gptme/commit/0266e350))
 - feat: added support for context_cmd project config ([#511](https://github.com/gptme/gptme/issues/511)) ([`8805aa28`](https://github.com/gptme/gptme/commit/8805aa28))
 - feat: added ChatConfig, refactored config, support overriding values ([#500](https://github.com/gptme/gptme/issues/500)) ([`fc6497c9`](https://github.com/gptme/gptme/commit/fc6497c9))
 - feat: added support for MCP server tools ([#486](https://github.com/gptme/gptme/issues/486)) ([`c43f10a6`](https://github.com/gptme/gptme/commit/c43f10a6))
 - feat: redesigned server API ([#469](https://github.com/gptme/gptme/issues/469)) ([`d8f4fc4a`](https://github.com/gptme/gptme/commit/d8f4fc4a))
 - feat: enhance computer tool with key sequence chaining for both macOS and Linux ([#479](https://github.com/gptme/gptme/issues/479)) ([`9f05e048`](https://github.com/gptme/gptme/commit/9f05e048))
 - feat: support entering newlines with Ctrl+J ([#464](https://github.com/gptme/gptme/issues/464)) ([`9204f1ab`](https://github.com/gptme/gptme/commit/9204f1ab))
 - feat: support LLM_PROXY_URL and LLM_PROXY_API_KEY (fixes [#470](https://github.com/gptme/gptme/issues/470)) ([#471](https://github.com/gptme/gptme/issues/471)) ([`1f2bd182`](https://github.com/gptme/gptme/commit/1f2bd182))

### 🐛 Fixes (90)
<details><summary>Click to expand</summary>
<p>

 - fix: use correct max_tokens for Anthropic models ([`b78bd34e`](https://github.com/gptme/gptme/commit/b78bd34e))
 - fix: unify thinking budget configuration in Anthropic LLM ([`f96f610f`](https://github.com/gptme/gptme/commit/f96f610f))
 - fix: updated metadata for gpt-5-mini and gpt-5-nano ([`72527978`](https://github.com/gptme/gptme/commit/72527978))
 - fix: updated metadata for opus 4/4.1 and sonnet 4 ([`0a5bd991`](https://github.com/gptme/gptme/commit/0a5bd991))
 - fix: add permissions to GitHub Actions eval workflow ([`92b6818d`](https://github.com/gptme/gptme/commit/92b6818d))
 - fix: add system dependencies for PyInstaller builds on Ubuntu ([`5a91a44d`](https://github.com/gptme/gptme/commit/5a91a44d))
 - fix: resolve _struct module error in PyInstaller build ([`dc4743db`](https://github.com/gptme/gptme/commit/dc4743db))
 - fix: support quoted heredoc delimiters with spaces and consolidate shell tests ([`9d2ff5e1`](https://github.com/gptme/gptme/commit/9d2ff5e1))
 - fix: switch to morph-v3-fast (morph-v2 deprecated) ([`051cdfcb`](https://github.com/gptme/gptme/commit/051cdfcb))
 - fix: fix /fork incorrectly recursively copying workspace directory ([`2db544f5`](https://github.com/gptme/gptme/commit/2db544f5))
 - fix: fixed bad syntax in dockerfile ([`52aacddd`](https://github.com/gptme/gptme/commit/52aacddd))
 - fix: add tree to gptme Dockerfile ([`5fcd296e`](https://github.com/gptme/gptme/commit/5fcd296e))
 - fix: set git config for gptme container so it can make commits ([`9f4cdcc7`](https://github.com/gptme/gptme/commit/9f4cdcc7))
 - fix: pass provider prefix when using LLM proxy ([`833a236d`](https://github.com/gptme/gptme/commit/833a236d))
 - fix: remove outdated print message ([`06dd8b53`](https://github.com/gptme/gptme/commit/06dd8b53))
 - fix: minor improvement to instructions for RAG tool ([`61853abf`](https://github.com/gptme/gptme/commit/61853abf))
 - fix: fixed running nested gptme sessions with shell tool waiting for piped stdin by redirecting stdin to /dev/null ([#604](https://github.com/gptme/gptme/issues/604)) ([`97110820`](https://github.com/gptme/gptme/commit/97110820))
 - fix: moved sound media ([`31f54127`](https://github.com/gptme/gptme/commit/31f54127))
 - fix: validate CLI tool arguments and provide clear error messages ([#600](https://github.com/gptme/gptme/issues/600)) ([`e7df4f5c`](https://github.com/gptme/gptme/commit/e7df4f5c))
 - fix: respect saved conversation config when resuming ([#595](https://github.com/gptme/gptme/issues/595)) ([`1d78ca30`](https://github.com/gptme/gptme/commit/1d78ca30))
 - fix: minor formatting fix in shell tool response to removing excessive newline ([`b03ec16b`](https://github.com/gptme/gptme/commit/b03ec16b))
 - fix: resolve OpenAPI validation errors with undefined dict schema ([`dcd05f9e`](https://github.com/gptme/gptme/commit/dcd05f9e))
 - fix: fix failing eval tests by correctly looking up agent name ([`6210bba6`](https://github.com/gptme/gptme/commit/6210bba6))
 - fix: complete tuple return type refactoring for precommit checks ([`8c98f140`](https://github.com/gptme/gptme/commit/8c98f140))
 - fix: fix rich console log formatting ([`be666183`](https://github.com/gptme/gptme/commit/be666183))
 - fix: workaround for incorrect pwd in new chats ([`daae7754`](https://github.com/gptme/gptme/commit/daae7754))
 - fix: disambiguate agent path and workspace ([`e4698c09`](https://github.com/gptme/gptme/commit/e4698c09))
 - fix: use shlex.split() for proper shell command parsing in agent creation ([`04931116`](https://github.com/gptme/gptme/commit/04931116))
 - fix: bad rebase ([`1e92afa5`](https://github.com/gptme/gptme/commit/1e92afa5))
 - fix: separate agent path and workspace ([`f21077f0`](https://github.com/gptme/gptme/commit/f21077f0))
 - fix: strip thinking when checking markdown for codeblocks ([#579](https://github.com/gptme/gptme/issues/579)) ([`b1c22f1f`](https://github.com/gptme/gptme/commit/b1c22f1f))
 - fix: morph should fail to apply if file changed since patch generated (edits before confirmation) ([#578](https://github.com/gptme/gptme/issues/578)) ([`2faa01f8`](https://github.com/gptme/gptme/commit/2faa01f8))
 - fix: improved mcp tool confirmation preview ([#577](https://github.com/gptme/gptme/issues/577)) ([`28c17214`](https://github.com/gptme/gptme/commit/28c17214))
 - fix: improvements to morph tool, better checks for valid results and output diff in response ([`c899e789`](https://github.com/gptme/gptme/commit/c899e789))
 - fix: update default models and add more gemini models ([`4835fdbf`](https://github.com/gptme/gptme/commit/4835fdbf))
 - fix: improve support for nested markdown codeblocks in markdown tool format ([#571](https://github.com/gptme/gptme/issues/571)) ([`d5b82d71`](https://github.com/gptme/gptme/commit/d5b82d71))
 - fix: add AGENTS.md to default context files ([`2a2a3ee0`](https://github.com/gptme/gptme/commit/2a2a3ee0))
 - fix: fix default `serve` command for gptme-server using click-default-group ([`ca5578a9`](https://github.com/gptme/gptme/commit/ca5578a9))
 - fix: improve id naming convention for new task conversations ([`0652c7e6`](https://github.com/gptme/gptme/commit/0652c7e6))
 - fix: move workspace prompt to get_prompt ([#521](https://github.com/gptme/gptme/issues/521)) ([`f0ab6faf`](https://github.com/gptme/gptme/commit/f0ab6faf))
 - fix: support heredocs with quoted delimiters in shell tool ([#558](https://github.com/gptme/gptme/issues/558)) ([`df09d8a1`](https://github.com/gptme/gptme/commit/df09d8a1))
 - fix: made assistant prompt name and color settable via env vars ([`315cec9d`](https://github.com/gptme/gptme/commit/315cec9d))
 - fix: improve shell command interrupt handling with partial output capture ([#556](https://github.com/gptme/gptme/issues/556)) ([`d5e89b6d`](https://github.com/gptme/gptme/commit/d5e89b6d))
 - fix: streamline gptme-util subcommands to all use list instead of ls ([`d6ef0b5e`](https://github.com/gptme/gptme/commit/d6ef0b5e))
 - fix: improve how tildes are handled in workspace paths (store/expose with tildes, process without) ([`bde855f2`](https://github.com/gptme/gptme/commit/bde855f2))
 - fix: resolve workspace path before saving config ([`63c00411`](https://github.com/gptme/gptme/commit/63c00411))
 - fix: include conversation workspace metadata in api ([#551](https://github.com/gptme/gptme/issues/551)) ([`ae7013c6`](https://github.com/gptme/gptme/commit/ae7013c6))
 - fix: fixed server tests ([`aaf80fdf`](https://github.com/gptme/gptme/commit/aaf80fdf))
 - fix: improvements to server ([#547](https://github.com/gptme/gptme/issues/547)) ([`077aa878`](https://github.com/gptme/gptme/commit/077aa878))
 - fix: fix config loading from cli params and env ([#545](https://github.com/gptme/gptme/issues/545)) ([`0d5a46d0`](https://github.com/gptme/gptme/commit/0d5a46d0))
 - fix: delay tts availability checks to tool init ([#543](https://github.com/gptme/gptme/issues/543)) ([`40ca7475`](https://github.com/gptme/gptme/commit/40ca7475))
 - fix: check if pre-commit executable exists before enabling checks ([`239c1a83`](https://github.com/gptme/gptme/commit/239c1a83))
 - fix: better support for reasoning via openrouter ([#538](https://github.com/gptme/gptme/issues/538)) ([`f8f1de95`](https://github.com/gptme/gptme/commit/f8f1de95))
 - fix: added support for TEMPERATURE and TOP_P environment variables ([`9c944e62`](https://github.com/gptme/gptme/commit/9c944e62))
 - fix: added shell example to use single quotes for complex content ([`e5f5d0f2`](https://github.com/gptme/gptme/commit/e5f5d0f2))
 - fix: fixed --workspace=@log behavior after config refactor ([#537](https://github.com/gptme/gptme/issues/537)) ([`b1824077`](https://github.com/gptme/gptme/commit/b1824077))
 - fix: disable pre-commit checks running before first user message, misc fixes ([`87781b6b`](https://github.com/gptme/gptme/commit/87781b6b))
 - fix: improve browser init logging, made ToolSpec.available optionally a Callable with new is_available property ([`01a8616e`](https://github.com/gptme/gptme/commit/01a8616e))
 - fix: change misleading config warning to debug level ([`23799d34`](https://github.com/gptme/gptme/commit/23799d34))
 - fix: output cwd changes in shell responses, error on extra ======= for patch ([#534](https://github.com/gptme/gptme/issues/534)) ([`7c4fbbb5`](https://github.com/gptme/gptme/commit/7c4fbbb5))
 - fix: use quadruple backticks in markdown codeblocks to avoid conflicts ([#533](https://github.com/gptme/gptme/issues/533)) ([`2abf6025`](https://github.com/gptme/gptme/commit/2abf6025))
 - fix: add metadata for Claude 4 models ([`c54ef4a2`](https://github.com/gptme/gptme/commit/c54ef4a2))
 - fix: improved text file detection in workspace API ([`350d8532`](https://github.com/gptme/gptme/commit/350d8532))
 - fix: dont fail pre-commit check if config file not found ([`76b55482`](https://github.com/gptme/gptme/commit/76b55482))
 - fix: updated with more Gemini models ([`e9c4cf3d`](https://github.com/gptme/gptme/commit/e9c4cf3d))
 - fix: reverted accidentally commited changes ([`7d9d0d92`](https://github.com/gptme/gptme/commit/7d9d0d92))
 - fix: added check for placeholder lines in save tool (and tests) ([#522](https://github.com/gptme/gptme/issues/522)) ([`b8d32b6b`](https://github.com/gptme/gptme/commit/b8d32b6b))
 - fix: added convert_convo.py script ([`344a1539`](https://github.com/gptme/gptme/commit/344a1539))
 - fix: better support for interrupting pre-commit checks ([`d50af6af`](https://github.com/gptme/gptme/commit/d50af6af))
 - fix: fix streaming files support in server ([#518](https://github.com/gptme/gptme/issues/518)) ([`543e2ee3`](https://github.com/gptme/gptme/commit/543e2ee3))
 - fix: fix use of workspace paths in server by syncing workspace set in chat config (new) with workspace symlink (old) ([#517](https://github.com/gptme/gptme/issues/517)) ([`984d6412`](https://github.com/gptme/gptme/commit/984d6412))
 - fix: use absolute paths for files in view_image messages, improve markdown markup for paths ([`f2f57c6e`](https://github.com/gptme/gptme/commit/f2f57c6e))
 - fix(server): don't store chat config obj in session, load mcp server from user/project config ([#515](https://github.com/gptme/gptme/issues/515)) ([`a6504e0e`](https://github.com/gptme/gptme/commit/a6504e0e))
 - fix: change mcp tool format to mcp_tool.mcp_func (instead of mcp_tool_mcp_func) ([`385901a3`](https://github.com/gptme/gptme/commit/385901a3))
 - fix: fixed prompt not showing when prompt text overflows ([#510](https://github.com/gptme/gptme/issues/510)) ([`e7657223`](https://github.com/gptme/gptme/commit/e7657223))
 - fix: remove default BASE ARG from Dockerfile.server to remove the need for skaffold workarounds ([#512](https://github.com/gptme/gptme/issues/512)) ([`a0b507ef`](https://github.com/gptme/gptme/commit/a0b507ef))
 - fix: misc fixes by mike ([#501](https://github.com/gptme/gptme/issues/501)) ([`adfd31f8`](https://github.com/gptme/gptme/commit/adfd31f8))
 - fix: fixes auto-stepping in server ([#499](https://github.com/gptme/gptme/issues/499)) ([`3b70dd8c`](https://github.com/gptme/gptme/commit/3b70dd8c))
 - fix: improved the gh-pr-view script and rewrote in Python ([#483](https://github.com/gptme/gptme/issues/483)) ([`ba032393`](https://github.com/gptme/gptme/commit/ba032393))
 - fix: change default gptme-server port to 5700 ([#491](https://github.com/gptme/gptme/issues/491)) ([`33c2ba7f`](https://github.com/gptme/gptme/commit/33c2ba7f))
 - fix: add screenshot example ([`d9be0727`](https://github.com/gptme/gptme/commit/d9be0727))
 - fix: fix weird needed {!r} in typecheck (https://github.com/gptme/gptme/pull/486#issuecomment-2763345203) ([`f5c58db0`](https://github.com/gptme/gptme/commit/f5c58db0))
 - fix: dont run `tree` outside git repositories (likely to timeout) ([`2fb0210d`](https://github.com/gptme/gptme/commit/2fb0210d))
 - fix: support credentials in CORS ([`958bcb89`](https://github.com/gptme/gptme/commit/958bcb89))
 - fix(server): support non-streaming LLM requests ([#481](https://github.com/gptme/gptme/issues/481)) ([`085b726b`](https://github.com/gptme/gptme/commit/085b726b))
 - fix: refactored tooluse detection in server to save a call ([`64888eb9`](https://github.com/gptme/gptme/commit/64888eb9))
 - fix(server): fixes interruptions during generation ([`adad1144`](https://github.com/gptme/gptme/commit/adad1144))
 - fix: fixed 'f-string expression part cannot include a backslash' ([`cbc69653`](https://github.com/gptme/gptme/commit/cbc69653))
 - fix: fix missing MODELS entry for azure (fixes [#472](https://github.com/gptme/gptme/issues/472)) ([`67cad0ac`](https://github.com/gptme/gptme/commit/67cad0ac))
 - fix(tts): correctly strip `<think>` tags and leading hashes for '# Headers' ([`7fecf09f`](https://github.com/gptme/gptme/commit/7fecf09f))

</p>
</details>

### 🔨 Misc (57)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.28.0 ([`41d31f10`](https://github.com/gptme/gptme/commit/41d31f10))
 - docs: updated README usage, CLI help strings, and set gpt-5 as default OpenAI model ([`b5a1abd7`](https://github.com/gptme/gptme/commit/b5a1abd7))
 - docs: clarify max_tokens calculation rationale for thinking feature ([`3395bf23`](https://github.com/gptme/gptme/commit/3395bf23))
 - docs: improved prometheus config and docs ([`037e51d4`](https://github.com/gptme/gptme/commit/037e51d4))
 - docs: improve setup instructions for agents (include uv) ([`4ac4a45f`](https://github.com/gptme/gptme/commit/4ac4a45f))
 - docs: add bench of startup time using hyperfine ([`a0c3ed39`](https://github.com/gptme/gptme/commit/a0c3ed39))
 - refactor: refactor to eliminate duplicate lines ([#608](https://github.com/gptme/gptme/issues/608)) ([`309ae85f`](https://github.com/gptme/gptme/commit/309ae85f))
 - docs: mention lessons system ([`1e8d62f3`](https://github.com/gptme/gptme/commit/1e8d62f3))
 - docs: fixed ref and improved note about agents in web ui ([`85680cf5`](https://github.com/gptme/gptme/commit/85680cf5))
 - docs: added mermaid diagrams to agents docs ([`63f2345e`](https://github.com/gptme/gptme/commit/63f2345e))
 - refactor: change how extra_headers and extra_data is set in openai client ([`860f1e89`](https://github.com/gptme/gptme/commit/860f1e89))
 - docs: link to llms.txt and llms-full.txt in docs index ([`e5b4fb97`](https://github.com/gptme/gptme/commit/e5b4fb97))
 - docs: improve github bot doc ([`d65c1ee4`](https://github.com/gptme/gptme/commit/d65c1ee4))
 - docs: minor formatting fix ([`2e9cf77b`](https://github.com/gptme/gptme/commit/2e9cf77b))
 - docs: add telemetry setup guide and simplify telemetry init ([`87cb53d4`](https://github.com/gptme/gptme/commit/87cb53d4))
 - docs: more updates to docs to recommend Claude Sonnet 4 ([`9cf99057`](https://github.com/gptme/gptme/commit/9cf99057))
 - docs: update docs to recommend Claude Sonnet 4 ([`2740eca5`](https://github.com/gptme/gptme/commit/2740eca5))
 - refactor: split api_v2.py into focused modules ([`c2b67525`](https://github.com/gptme/gptme/commit/c2b67525))
 - refactor: break down complex functions into smaller focused units ([`2207190c`](https://github.com/gptme/gptme/commit/2207190c))
 - refactor: reduce code duplication with CommandContext ([`c884d8d1`](https://github.com/gptme/gptme/commit/c884d8d1))
 - refactor: convert command handling to decorator-based registry ([`04a6af0d`](https://github.com/gptme/gptme/commit/04a6af0d))
 - refactor: simplify chat function signatures and improve autocommit prompting ([`a37ddf7c`](https://github.com/gptme/gptme/commit/a37ddf7c))
 - refactor: simplify chat loop and fix interruption handling ([#582](https://github.com/gptme/gptme/issues/582)) ([`a012815e`](https://github.com/gptme/gptme/commit/a012815e))
 -  feat: add create agent endpoint in v2 api ([`c2759a5a`](https://github.com/gptme/gptme/commit/c2759a5a))
 - refactor: improve error handling and message formatting in morph/patch tools ([#581](https://github.com/gptme/gptme/issues/581)) ([`5b19708b`](https://github.com/gptme/gptme/commit/5b19708b))
 - chore: improve gitignore and dockerignore ([`ab9627a0`](https://github.com/gptme/gptme/commit/ab9627a0))
 - docs: updated docs ([#564](https://github.com/gptme/gptme/issues/564)) ([`f0842e84`](https://github.com/gptme/gptme/commit/f0842e84))
 - docs: added sphinx_llms_txt extension to generate llms.txt and llms-full.txt ([#561](https://github.com/gptme/gptme/issues/561)) ([`8181e276`](https://github.com/gptme/gptme/commit/8181e276))
 - tests: fixed test ([`a78c0561`](https://github.com/gptme/gptme/commit/a78c0561))
 - docs: fix duplicate autosectionlabel warnings by limiting maxdepth ([`5ca5e83b`](https://github.com/gptme/gptme/commit/5ca5e83b))
 - docs: added back missing file from last PR ([`d5507b7e`](https://github.com/gptme/gptme/commit/d5507b7e))
 - docs: mention Ctrl+X Ctrl+E support in --help output ([`ae3d2857`](https://github.com/gptme/gptme/commit/ae3d2857))
 - docs: improve alternatives, config and usage documentation ([`4d310b83`](https://github.com/gptme/gptme/commit/4d310b83))
 - docs: changed MCP example to use mcp-server-sqlite ([`d7d9470a`](https://github.com/gptme/gptme/commit/d7d9470a))
 - docs: moved environment variables section to bottom of config doc, add link to it from lead ([`f83568a4`](https://github.com/gptme/gptme/commit/f83568a4))
 - docs: improved docs for config, including context_cmd description and Chat Config section ([`46a4628c`](https://github.com/gptme/gptme/commit/46a4628c))
 - refactor: thread-local globals for _config and _loaded_tools ([#506](https://github.com/gptme/gptme/issues/506)) ([`ad888d38`](https://github.com/gptme/gptme/commit/ad888d38))
 - docs: improved docs, added docstrings and minor improvements to config code ([#505](https://github.com/gptme/gptme/issues/505)) ([`c0c5c739`](https://github.com/gptme/gptme/commit/c0c5c739))
 - refactor: improve config and tools architecture ([#503](https://github.com/gptme/gptme/issues/503)) ([`dc7be5fa`](https://github.com/gptme/gptme/commit/dc7be5fa))
 - docs: add a copiable markdown for powerd by gptme badge ([#490](https://github.com/gptme/gptme/issues/490)) ([`0b3a3967`](https://github.com/gptme/gptme/commit/0b3a3967))
 - docs: mention mcp in custom_tool doc ([`b24c0f95`](https://github.com/gptme/gptme/commit/b24c0f95))
 - tests: mark tests failing in ci with xfail ([`ed0e8ada`](https://github.com/gptme/gptme/commit/ed0e8ada))
 - docs: strip ansi color codes from jscpd output ([`5396094e`](https://github.com/gptme/gptme/commit/5396094e))
 - docs: improved MPC docs ([`150a35b5`](https://github.com/gptme/gptme/commit/150a35b5))
 - tests: improved mcp tests, replaced weather mcp server test with memory server ([`c7f7014b`](https://github.com/gptme/gptme/commit/c7f7014b))
 - docs: fix crazy wrong MCP explanation in docs ([`ff52d7ba`](https://github.com/gptme/gptme/commit/ff52d7ba))
 - docs: fixed title underline too short ([`15c3f403`](https://github.com/gptme/gptme/commit/15c3f403))
 - docs: moved MCP guide into documentation ([`078e667f`](https://github.com/gptme/gptme/commit/078e667f))
 - tests: add xfail to flaky tests ([`6ddf8a3b`](https://github.com/gptme/gptme/commit/6ddf8a3b))
 - refactor: reorder Message attributes ([`22f590ab`](https://github.com/gptme/gptme/commit/22f590ab))
 - chore: add dev/ to .gitignore for dev branch worktrees ([`c19893b3`](https://github.com/gptme/gptme/commit/c19893b3))
 - docs: add jscpd output to `make metrics` and arewetiny doc ([`50bcae47`](https://github.com/gptme/gptme/commit/50bcae47))
 - docs: added sitemap.xml ([#480](https://github.com/gptme/gptme/issues/480)) ([`5d4df786`](https://github.com/gptme/gptme/commit/5d4df786))
 - docs: improved timeline further ([`2665a9e3`](https://github.com/gptme/gptme/commit/2665a9e3))
 - docs: fixed rst list formatting, added `check_rst_formatting.py` script and added to pre-commit hooks ([`e88bd567`](https://github.com/gptme/gptme/commit/e88bd567))
 - docs: updated timeline ([`34bddb2b`](https://github.com/gptme/gptme/commit/34bddb2b))
 - docs: improved tts usage docs ([`598e1b29`](https://github.com/gptme/gptme/commit/598e1b29))

</p>
</details>

*(excluded 19 less relevant [commits](https://github.com/gptme/gptme/compare/v0.27.0...v0.28.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.27.0...v0.28.0
