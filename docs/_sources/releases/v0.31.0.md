# v0.31.0

These are the release notes for gptme version v0.31.0.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare, @nikongo, @TimeToBuildBob

## Changelog

Changes since v0.30.0:


## 📦 gptme

### ✨ Features (25)

 - feat(lessons): auto-discover lessons from plugins ([#944](https://github.com/gptme/gptme/issues/944)) ([`ab531ad20`](https://github.com/gptme/gptme/commit/ab531ad20))
 - feat(message): add MessageMetadata TypedDict for token/cost tracking ([#943](https://github.com/gptme/gptme/issues/943)) ([`684bbb6ff`](https://github.com/gptme/gptme/commit/684bbb6ff))
 - feat(cost): implement cost_awareness hook for session cost tracking ([#939](https://github.com/gptme/gptme/issues/939)) ([`ed409cb26`](https://github.com/gptme/gptme/commit/ed409cb26))
 - feat(telemetry): improve trace quality with context propagation and rich metrics ([#942](https://github.com/gptme/gptme/issues/942)) ([`5be5a39d4`](https://github.com/gptme/gptme/commit/5be5a39d4))
 - feat(shell): add background job support for long-running commands ([#902](https://github.com/gptme/gptme/issues/902)) ([`c48bf9bd7`](https://github.com/gptme/gptme/commit/c48bf9bd7))
 - feat(setup): validate API keys before saving (Issue [#930](https://github.com/gptme/gptme/issues/930)) ([#931](https://github.com/gptme/gptme/issues/931)) ([`bf1bc716b`](https://github.com/gptme/gptme/commit/bf1bc716b))
 - feat(lessons): add caching and deduplication to lesson index ([#928](https://github.com/gptme/gptme/issues/928)) ([`3fdbf4186`](https://github.com/gptme/gptme/commit/3fdbf4186))
 - feat(hooks): add form auto-detection hook (Issue [#591](https://github.com/gptme/gptme/issues/591)) ([#919](https://github.com/gptme/gptme/issues/919)) ([`40dc68f45`](https://github.com/gptme/gptme/commit/40dc68f45))
 - feat(logmanager): add content-addressable file storage (Issue [#150](https://github.com/gptme/gptme/issues/150)) ([#913](https://github.com/gptme/gptme/issues/913)) ([`86febf7de`](https://github.com/gptme/gptme/commit/86febf7de))
 - feat(tools): add form tool for structured user input (Issue [#591](https://github.com/gptme/gptme/issues/591)) ([#911](https://github.com/gptme/gptme/issues/911)) ([`92c475a47`](https://github.com/gptme/gptme/commit/92c475a47))
 - feat: add Docker container for GitHub bot (Issue [#305](https://github.com/gptme/gptme/issues/305)) ([#905](https://github.com/gptme/gptme/issues/905)) ([`ec1204fa9`](https://github.com/gptme/gptme/commit/ec1204fa9))
 - feat(tmux): add wait command to monitor long-running commands ([#901](https://github.com/gptme/gptme/issues/901)) ([`0ee75e544`](https://github.com/gptme/gptme/commit/0ee75e544))
 - feat(prompt): add multi-line input support ([#899](https://github.com/gptme/gptme/issues/899)) ([`558ee3b5f`](https://github.com/gptme/gptme/commit/558ee3b5f))
 - feat(autocompact): add Phase 3 extractive compression for long messages ([#886](https://github.com/gptme/gptme/issues/886)) ([`cca057c63`](https://github.com/gptme/gptme/commit/cca057c63))
 - feat: add diagnostic logging to shell tool for Issue [#408](https://github.com/gptme/gptme/issues/408) ([#890](https://github.com/gptme/gptme/issues/890)) ([`750009193`](https://github.com/gptme/gptme/commit/750009193))
 - feat: support GPTME_TTS_SPEED and set exclude-newer for tts_server.py script to fix issues ([`eed3a987c`](https://github.com/gptme/gptme/commit/eed3a987c))
 - feat(llm): add constrained decoding support ([#776](https://github.com/gptme/gptme/issues/776)) ([`94621288d`](https://github.com/gptme/gptme/commit/94621288d))
 - feat: add Cursor .mdc rules support (Issue [#686](https://github.com/gptme/gptme/issues/686) Phase 5) ([#882](https://github.com/gptme/gptme/issues/882)) ([`59723c493`](https://github.com/gptme/gptme/commit/59723c493))
 - feat: Enhanced plugin management with smart src/ layout discovery ([#873](https://github.com/gptme/gptme/issues/873)) ([`0ccdfbe44`](https://github.com/gptme/gptme/commit/0ccdfbe44))
 - feat: add compression analysis utilities and script ([#864](https://github.com/gptme/gptme/issues/864)) ([`fd3d8edc2`](https://github.com/gptme/gptme/commit/fd3d8edc2))
 - feat(eval): GEPA Week 3 - HybridOptimizer with adaptive multi-stage optimization ([#859](https://github.com/gptme/gptme/issues/859)) ([`58cce70f8`](https://github.com/gptme/gptme/commit/58cce70f8))
 - feat(context): implement hooks-based context compression architecture ([#844](https://github.com/gptme/gptme/issues/844)) ([`20435f324`](https://github.com/gptme/gptme/commit/20435f324))
 - feat(context): implement Phase 3.1.1 core infrastructure ([#860](https://github.com/gptme/gptme/issues/860)) ([`b898c8550`](https://github.com/gptme/gptme/commit/b898c8550))
 - feat: implement active context discovery ([#856](https://github.com/gptme/gptme/issues/856)) ([`990fdca36`](https://github.com/gptme/gptme/commit/990fdca36))
 - feat: add restart tool ([#853](https://github.com/gptme/gptme/issues/853)) ([`418532ea8`](https://github.com/gptme/gptme/commit/418532ea8))

### 🐛 Fixes (50)
<details><summary>Click to expand</summary>
<p>

 - fix(config): remove quiet parameter from cache key to prevent duplicate entries ([`61b81b0f5`](https://github.com/gptme/gptme/commit/61b81b0f5))
 - fix: mark plugins as loaded even if no tool_modules ([`8917b180f`](https://github.com/gptme/gptme/commit/8917b180f))
 - fix: more fixes to invalid unicode handling in shell tool ([`65151ee60`](https://github.com/gptme/gptme/commit/65151ee60))
 - fix: improved style of initial output ([`06730c6bb`](https://github.com/gptme/gptme/commit/06730c6bb))
 - fix: fix invalid unicode handling in shell tool ([`d3b7ddd87`](https://github.com/gptme/gptme/commit/d3b7ddd87))
 - fix: hide token/time/cost awareness messages by default ([`755070737`](https://github.com/gptme/gptme/commit/755070737))
 - fix: suppress duplicate config log when loading conversation metadata ([`10148567b`](https://github.com/gptme/gptme/commit/10148567b))
 - fix: reduce plugin logging spam by logging each plugin once when loaded ([`8e50bcde7`](https://github.com/gptme/gptme/commit/8e50bcde7))
 - fix: handle interrupt detection when hooks add messages after interrupt ([`216f274da`](https://github.com/gptme/gptme/commit/216f274da))
 - fix(autocompact): add minimum savings threshold to avoid wasteful compaction ([#946](https://github.com/gptme/gptme/issues/946)) ([`2f5b7825c`](https://github.com/gptme/gptme/commit/2f5b7825c))
 - fix: suppress telemetry token warnings ([`2aa146e46`](https://github.com/gptme/gptme/commit/2aa146e46))
 - fix: fixed metadata for xai models ([`d9c42f2ba`](https://github.com/gptme/gptme/commit/d9c42f2ba))
 - fix: improved agent setup instructions with more extensive extra/optional/recommended packages ([`4f5b64dab`](https://github.com/gptme/gptme/commit/4f5b64dab))
 - fix: handle unquoted glob patterns in Cursor .mdc files ([`b09f74dca`](https://github.com/gptme/gptme/commit/b09f74dca))
 - fix(telemetry): remove noisy codeblock tracing (Issue [#199](https://github.com/gptme/gptme/issues/199)) ([#936](https://github.com/gptme/gptme/issues/936)) ([`c36e25089`](https://github.com/gptme/gptme/commit/c36e25089))
 - fix: capture both stdout and stderr from context_cmd on error ([#933](https://github.com/gptme/gptme/issues/933)) ([`4e81c9187`](https://github.com/gptme/gptme/commit/4e81c9187))
 - fix: attribute perplexity use to gptme on openrouter ([#929](https://github.com/gptme/gptme/issues/929)) ([`50792e65f`](https://github.com/gptme/gptme/commit/50792e65f))
 - fix: fix prompt_systeminfo to work on android/termux ([`e5fa38fd8`](https://github.com/gptme/gptme/commit/e5fa38fd8))
 - fix(tests): clean up gptme_N sessions in tmux test fixture ([#926](https://github.com/gptme/gptme/issues/926)) ([`dbe1676e4`](https://github.com/gptme/gptme/commit/dbe1676e4))
 - fix: read [prompt] section in project config ([#927](https://github.com/gptme/gptme/issues/927)) ([`1ee053ad1`](https://github.com/gptme/gptme/commit/1ee053ad1))
 - fix(telemetry): filter NotGiven attribute warnings from OTEL instrumentation ([#925](https://github.com/gptme/gptme/issues/925)) ([`a50b70623`](https://github.com/gptme/gptme/commit/a50b70623))
 - fix(tmux): truncate long pane output to prevent context overflow (Issue [#923](https://github.com/gptme/gptme/issues/923)) ([#924](https://github.com/gptme/gptme/issues/924)) ([`7590830b8`](https://github.com/gptme/gptme/commit/7590830b8))
 - fix(llm): add empty reasoning_content field for DeepSeek assistant messages with tool_calls ([#918](https://github.com/gptme/gptme/issues/918)) ([`20f4888cf`](https://github.com/gptme/gptme/commit/20f4888cf))
 - fix(tests): add cleanup fixtures for ShellSession and subagents (Issue [#910](https://github.com/gptme/gptme/issues/910)) ([#912](https://github.com/gptme/gptme/issues/912)) ([`980917817`](https://github.com/gptme/gptme/commit/980917817))
 - fix(mcp): preserve server process when conversation is interrupted ([#914](https://github.com/gptme/gptme/issues/914)) ([`c4b59c240`](https://github.com/gptme/gptme/commit/c4b59c240))
 - fix(browser): improve error messages for search bot detection ([#904](https://github.com/gptme/gptme/issues/904)) ([`ba3c0233f`](https://github.com/gptme/gptme/commit/ba3c0233f))
 - fix(config): remove assertions requiring prompt/env in user config ([#909](https://github.com/gptme/gptme/issues/909)) ([`06a451433`](https://github.com/gptme/gptme/commit/06a451433))
 - fix(codeblock): handle nested codeblocks with same language tag ([#903](https://github.com/gptme/gptme/issues/903)) ([`79fdd6ea9`](https://github.com/gptme/gptme/commit/79fdd6ea9))
 - fix(shell): prevent output mixing between commands (Issue [#408](https://github.com/gptme/gptme/issues/408)) ([#906](https://github.com/gptme/gptme/issues/906)) ([`25dac14f2`](https://github.com/gptme/gptme/commit/25dac14f2))
 - fix(llm): handle mixed content types in Groq/DeepSeek transformation ([#896](https://github.com/gptme/gptme/issues/896)) ([`31ef55c01`](https://github.com/gptme/gptme/commit/31ef55c01))
 - fix(message): resolve file paths to absolute when serializing ([#898](https://github.com/gptme/gptme/issues/898)) ([`4a62f837d`](https://github.com/gptme/gptme/commit/4a62f837d))
 - fix(message): escape Rich markup in non-code-block content ([#894](https://github.com/gptme/gptme/issues/894)) ([`369c72c32`](https://github.com/gptme/gptme/commit/369c72c32))
 - fix(lessons): lazy-load ACE to prevent import warnings ([#893](https://github.com/gptme/gptme/issues/893)) ([`5e5557d05`](https://github.com/gptme/gptme/commit/5e5557d05))
 - fix: support custom providers in model selection and routing ([#891](https://github.com/gptme/gptme/issues/891)) ([`f20bdc18a`](https://github.com/gptme/gptme/commit/f20bdc18a))
 - fix: add browser recovery logic to prevent deadlocks on connection errors ([#888](https://github.com/gptme/gptme/issues/888)) ([`ede5080f2`](https://github.com/gptme/gptme/commit/ede5080f2))
 - fix: prevent file content inclusion in command arguments ([#889](https://github.com/gptme/gptme/issues/889)) ([`a665571df`](https://github.com/gptme/gptme/commit/a665571df))
 - fix: prettier errors on fatal exceptions ([`d9f2c06e2`](https://github.com/gptme/gptme/commit/d9f2c06e2))
 - fix: fix toolcall format to allow dots in call_id (fixes Kimi K2) ([`6e970e546`](https://github.com/gptme/gptme/commit/6e970e546))
 - fix: add better cost telemetry logging in optimizable scenarios ([#883](https://github.com/gptme/gptme/issues/883)) ([`7092eae59`](https://github.com/gptme/gptme/commit/7092eae59))
 - fix: added claude-opus-4-5 ([`4a9363a4b`](https://github.com/gptme/gptme/commit/4a9363a4b))
 - fix(tests): explicitly disable chat history in server v2 test ([#872](https://github.com/gptme/gptme/issues/872)) ([`b888abbea`](https://github.com/gptme/gptme/commit/b888abbea))
 - fix(shell): added SC1011 and SC1073 to shellcheck error codes ([`4acc3748e`](https://github.com/gptme/gptme/commit/4acc3748e))
 - fix: skip large files in active_context hook ([`ecc1da20d`](https://github.com/gptme/gptme/commit/ecc1da20d))
 - fix: detect git version from pip's direct_url.json for pipx installs ([#871](https://github.com/gptme/gptme/issues/871)) ([`f557c43b8`](https://github.com/gptme/gptme/commit/f557c43b8))
 - fix(patch): implement relaxed whitespace matching for whitespace-only lines ([#861](https://github.com/gptme/gptme/issues/861)) ([`6681751aa`](https://github.com/gptme/gptme/commit/6681751aa))
 - fix(server): add default model fallback and improve error messages ([#863](https://github.com/gptme/gptme/issues/863)) ([`ebdd3df98`](https://github.com/gptme/gptme/commit/ebdd3df98))
 - fix(shell): properly terminate child processes on timeout ([#868](https://github.com/gptme/gptme/issues/868)) ([`00377049a`](https://github.com/gptme/gptme/commit/00377049a))
 - fix(dspy): register metadata for complexity test tasks ([#867](https://github.com/gptme/gptme/issues/867)) ([`767264944`](https://github.com/gptme/gptme/commit/767264944))
 - fix(tools): preserve full type information in tool signatures ([#865](https://github.com/gptme/gptme/issues/865)) ([`10ddeb6cb`](https://github.com/gptme/gptme/commit/10ddeb6cb))
 - fix: initialize tools and model in subagent threads ([#854](https://github.com/gptme/gptme/issues/854)) ([`389549c7f`](https://github.com/gptme/gptme/commit/389549c7f))

</p>
</details>

### 🔨 Misc (22)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.31.0 ([`a211c2a1f`](https://github.com/gptme/gptme/commit/a211c2a1f))
 - docs: add TODO comment to prompts.py about optimizing prompt caching of context_cmd ([`63c998da5`](https://github.com/gptme/gptme/commit/63c998da5))
 - style: use consistent 'Using' prefix for local config log message ([`4fbcf8927`](https://github.com/gptme/gptme/commit/4fbcf8927))
 - docs(bot): improve GitHub bot documentation ([#938](https://github.com/gptme/gptme/issues/938)) ([`bc3929e4e`](https://github.com/gptme/gptme/commit/bc3929e4e))
 - chore: small fixes and formatting ([#934](https://github.com/gptme/gptme/issues/934)) ([`0e7edef0d`](https://github.com/gptme/gptme/commit/0e7edef0d))
 - docs: updated agent setup guide ([`8d1b0b88d`](https://github.com/gptme/gptme/commit/8d1b0b88d))
 - chore(config): disable fresh context mode ([`f71558992`](https://github.com/gptme/gptme/commit/f71558992))
 - docs: add issue labeling guide to contributing docs (Issue [#874](https://github.com/gptme/gptme/issues/874)) ([#922](https://github.com/gptme/gptme/issues/922)) ([`606e7ead8`](https://github.com/gptme/gptme/commit/606e7ead8))
 - refactor(bot): simplify action.yml to use github_bot.py script (Issue [#305](https://github.com/gptme/gptme/issues/305)) ([#915](https://github.com/gptme/gptme/issues/915)) ([`1e3c020d9`](https://github.com/gptme/gptme/commit/1e3c020d9))
 - docs: add optional system dependencies section ([#897](https://github.com/gptme/gptme/issues/897)) ([`2718cb369`](https://github.com/gptme/gptme/commit/2718cb369))
 - Fix: Use conservative token limit for Anthropic models to prevent overflow ([#887](https://github.com/gptme/gptme/issues/887)) ([`f94ecc3cb`](https://github.com/gptme/gptme/commit/f94ecc3cb))
 - docs: added more model alternatives and notes on model and provider selection ([`c7b5fd8f7`](https://github.com/gptme/gptme/commit/c7b5fd8f7))
 - docs: Phase 4 - Add plugin example links and decision guidance ([#881](https://github.com/gptme/gptme/issues/881)) ([`746fe1d04`](https://github.com/gptme/gptme/commit/746fe1d04))
 - docs: clarify skills vs plugins architecture ([#880](https://github.com/gptme/gptme/issues/880)) ([`66168d633`](https://github.com/gptme/gptme/commit/66168d633))
 - refactor: Adopt Anthropic skill format (replaces [#876](https://github.com/gptme/gptme/issues/876)) ([#877](https://github.com/gptme/gptme/issues/877)) ([`8d8faca55`](https://github.com/gptme/gptme/commit/8d8faca55))
 - test: mark flaky test as flaky ([`6b91b0d5f`](https://github.com/gptme/gptme/commit/6b91b0d5f))
 - test(dspy): add comprehensive unit tests for GptmeReasoningProgram ([#870](https://github.com/gptme/gptme/issues/870)) ([`8a8c04e8e`](https://github.com/gptme/gptme/commit/8a8c04e8e))
 - docs(dspy): add comprehensive documentation for use_reasoning_program parameter ([#869](https://github.com/gptme/gptme/issues/869)) ([`f81301090`](https://github.com/gptme/gptme/commit/f81301090))
 - tests: add xfail to flaky test ([`8f609310f`](https://github.com/gptme/gptme/commit/8f609310f))
 - docs: more docs fixes ([#852](https://github.com/gptme/gptme/issues/852)) ([`15550cb1d`](https://github.com/gptme/gptme/commit/15550cb1d))
 - docs: custom provider docs fix ([#851](https://github.com/gptme/gptme/issues/851)) ([`b57b22b68`](https://github.com/gptme/gptme/commit/b57b22b68))
 - docs: fixes to docs, clean up ([#850](https://github.com/gptme/gptme/issues/850)) ([`0464aad99`](https://github.com/gptme/gptme/commit/0464aad99))

</p>
</details>

*(excluded 4 less relevant [commits](https://github.com/gptme/gptme/compare/v0.30.0...v0.31.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.30.0...v0.31.0
