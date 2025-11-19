# v0.30.0

These are the release notes for gptme version v0.30.0.

## Contributors

Thanks to everyone who contributed to this release:

@0xbrayo, @erikbjare, @TimeToBuildBob

## Changelog

Changes since v0.29.0:


## 📦 gptme

### ✨ Features (26)

 - feat(context): Phase 1 - Core Context Selector Infrastructure ([#831](https://github.com/gptme/gptme/issues/831)) ([`71416f6c`](https://github.com/gptme/gptme/commit/71416f6c))
 - feat: convert default model and hook registry to ContextVar for thread safety ([#848](https://github.com/gptme/gptme/issues/848)) ([`5a1ac7b9`](https://github.com/gptme/gptme/commit/5a1ac7b9))
 - feat(plugins): implement Phase 3 command integration ([#846](https://github.com/gptme/gptme/issues/846)) ([`c7304c43`](https://github.com/gptme/gptme/commit/c7304c43))
 - feat(plugins): implement Phase 2 hook integration ([#845](https://github.com/gptme/gptme/issues/845)) ([`e86fe8e0`](https://github.com/gptme/gptme/commit/e86fe8e0))
 - feat(plugins): implement Phase 1 plugin infrastructure ([#843](https://github.com/gptme/gptme/issues/843)) ([`fe8b355c`](https://github.com/gptme/gptme/commit/fe8b355c))
 - feat(server): add API v2 client for programmatic interaction ([#841](https://github.com/gptme/gptme/issues/841)) ([`dfd9f74f`](https://github.com/gptme/gptme/commit/dfd9f74f))
 - feat: auto-enable complete tool in non-interactive mode and support multiple -t flags ([#836](https://github.com/gptme/gptme/issues/836)) ([`a5ccb6cd`](https://github.com/gptme/gptme/commit/a5ccb6cd))
 - feat(gepa): Phase 2 test set expansion - 7 more tasks ([#833](https://github.com/gptme/gptme/issues/833)) ([`7bbce067`](https://github.com/gptme/gptme/commit/7bbce067))
 - feat(gepa): implement Phase 1 test set expansion (12 new tasks) ([#832](https://github.com/gptme/gptme/issues/832)) ([`68bd60cf`](https://github.com/gptme/gptme/commit/68bd60cf))
 - feat: support OPENROUTER_API_KEY for perplexity ([#828](https://github.com/gptme/gptme/issues/828)) ([`658c4450`](https://github.com/gptme/gptme/commit/658c4450))
 - feat(validation): add MESSAGE_POST_PROCESS hook for markdown codeblock cut-off detection ([#824](https://github.com/gptme/gptme/issues/824)) ([`ed01e1cb`](https://github.com/gptme/gptme/commit/ed01e1cb))
 - feat: add support for gptme.local.toml configuration layering ([#617](https://github.com/gptme/gptme/issues/617)) ([`3dfcc9e5`](https://github.com/gptme/gptme/commit/3dfcc9e5))
 - feat(gepa): add PromptImprovementModule to fix InputField architecture issue ([#823](https://github.com/gptme/gptme/issues/823)) ([`825d71aa`](https://github.com/gptme/gptme/commit/825d71aa))
 - feat(lessons): Phase 5.5 - Dynamic top-K selection ([#820](https://github.com/gptme/gptme/issues/820)) ([`7d7fcc45`](https://github.com/gptme/gptme/commit/7d7fcc45))
 - feat(lessons): Add ACE-inspired hybrid lesson matching ([#817](https://github.com/gptme/gptme/issues/817)) ([`1bdd9de5`](https://github.com/gptme/gptme/commit/1bdd9de5))
 - feat: add hook support to server API v2 ([#769](https://github.com/gptme/gptme/issues/769)) ([`104aacff`](https://github.com/gptme/gptme/commit/104aacff))
 - feat(llm): add custom OpenAI-compatible providers support ([#800](https://github.com/gptme/gptme/issues/800)) ([`cdf548ee`](https://github.com/gptme/gptme/commit/cdf548ee))
 - feat(subagent): add planner mode for task delegation ([#753](https://github.com/gptme/gptme/issues/753)) ([`71b72b9b`](https://github.com/gptme/gptme/commit/71b72b9b))
 - feat(server): auto-generate auth token and document security risks ([#803](https://github.com/gptme/gptme/issues/803)) ([`f652e752`](https://github.com/gptme/gptme/commit/f652e752))
 - feat(hooks): improve typing for hook registration with Protocol overloads ([#801](https://github.com/gptme/gptme/issues/801)) ([`7bb17df7`](https://github.com/gptme/gptme/commit/7bb17df7))
 - feat(eval): implement Docker-based execution environment ([#791](https://github.com/gptme/gptme/issues/791)) ([`7c676168`](https://github.com/gptme/gptme/commit/7c676168))
 - feat(dspy): implement multi-stage reasoning program for GEPA ([#786](https://github.com/gptme/gptme/issues/786)) ([`a7f87c58`](https://github.com/gptme/gptme/commit/a7f87c58))
 - feat: log a warning when context command output is large ([#787](https://github.com/gptme/gptme/issues/787)) ([`cf741f84`](https://github.com/gptme/gptme/commit/cf741f84))
 - feat(server): implement token-based authentication for dev environment ([#782](https://github.com/gptme/gptme/issues/782)) ([`65507d7b`](https://github.com/gptme/gptme/commit/65507d7b))
 - feat(lessons): add Cursor rules integration and project-local lessons support ([#779](https://github.com/gptme/gptme/issues/779)) ([`2ddc0e00`](https://github.com/gptme/gptme/commit/2ddc0e00))
 - feat(tools/shell): store truncated output before discarding ([#775](https://github.com/gptme/gptme/issues/775)) ([`b7c44982`](https://github.com/gptme/gptme/commit/b7c44982))

### 🐛 Fixes (30)
<details><summary>Click to expand</summary>
<p>

 - fix: added SC2016 as excluded shellcheck code ([`436b335d`](https://github.com/gptme/gptme/commit/436b335d))
 - fix: fix broken favicon in the root path ([#847](https://github.com/gptme/gptme/issues/847)) ([`421194a1`](https://github.com/gptme/gptme/commit/421194a1))
 - fix: remove xfail markers from previously flaky server tests ([#849](https://github.com/gptme/gptme/issues/849)) ([`1645926d`](https://github.com/gptme/gptme/commit/1645926d))
 - fix(gepa): Phase 3.3 - Fix task source and auto parameter conflicts ([#837](https://github.com/gptme/gptme/issues/837)) ([`dbbbc849`](https://github.com/gptme/gptme/commit/dbbbc849))
 - fix(security): block command injection via pipe-to-shell patterns ([#840](https://github.com/gptme/gptme/issues/840)) ([`87cd354d`](https://github.com/gptme/gptme/commit/87cd354d))
 - fix(gepa): remove auto parameter conflict in MIPROv2 ([#835](https://github.com/gptme/gptme/issues/835)) ([`7e51e0ac`](https://github.com/gptme/gptme/commit/7e51e0ac))
 - fix: fixed Kimi K2 thinking toolcall support via OpenRouter ([#830](https://github.com/gptme/gptme/issues/830)) ([`5a758dbc`](https://github.com/gptme/gptme/commit/5a758dbc))
 - fix: resolve MCP tool loading issues and connection errors ([#825](https://github.com/gptme/gptme/issues/825)) ([`72a4d85b`](https://github.com/gptme/gptme/commit/72a4d85b))
 - fix(subagent): add missing tool_format parameter to chat() calls ([`3167b336`](https://github.com/gptme/gptme/commit/3167b336))
 - fix(gepa): export ANTHROPIC_API_KEY and suppress verbose logs ([#821](https://github.com/gptme/gptme/issues/821)) ([`aeefe9b4`](https://github.com/gptme/gptme/commit/aeefe9b4))
 - fix(gepa): collect and save trajectory feedback in optimization results ([#819](https://github.com/gptme/gptme/issues/819)) ([`603ccfef`](https://github.com/gptme/gptme/commit/603ccfef))
 - fix(eval): fix reflection model used in GEPA optimizer ([#814](https://github.com/gptme/gptme/issues/814)) ([`6345f0c2`](https://github.com/gptme/gptme/commit/6345f0c2))
 - fix(eval): add defensive check for eval_result in task_success_metric ([#813](https://github.com/gptme/gptme/issues/813)) ([`644eadd9`](https://github.com/gptme/gptme/commit/644eadd9))
 - fix(server): add GPTME_DISABLE_AUTH env var for k8s deployments ([#811](https://github.com/gptme/gptme/issues/811)) ([`dd491017`](https://github.com/gptme/gptme/commit/dd491017))
 - fix(tmux): use dashes instead of underscores for tmux tool function ([#810](https://github.com/gptme/gptme/issues/810)) ([`e1119abe`](https://github.com/gptme/gptme/commit/e1119abe))
 - fix(shell): added SC2002 to default shellcheck excludes ([`3c088d37`](https://github.com/gptme/gptme/commit/3c088d37))
 - fix: improve shell error handling and add Anthropic debug logging ([`cdd90afb`](https://github.com/gptme/gptme/commit/cdd90afb))
 - fix: fix session start hook msgs not being persisted ([#808](https://github.com/gptme/gptme/issues/808)) ([`43a8c55d`](https://github.com/gptme/gptme/commit/43a8c55d))
 - fix(eval): run gptme agent inside Docker when --use-docker is used ([#805](https://github.com/gptme/gptme/issues/805)) ([`84f18eb3`](https://github.com/gptme/gptme/commit/84f18eb3))
 - fix(shell): handle bashlex parsing errors for bash builtins like 'time' ([#799](https://github.com/gptme/gptme/issues/799)) ([`0e18bf53`](https://github.com/gptme/gptme/commit/0e18bf53))
 - fix(shell): added rg, ag, ast-grep, hyperfine to mentioned installed shell commands ([`3c2b3f8e`](https://github.com/gptme/gptme/commit/3c2b3f8e))
 - fix(tests): update test_auto_compact.py for timestamp-based naming ([#797](https://github.com/gptme/gptme/issues/797)) ([`8142f895`](https://github.com/gptme/gptme/commit/8142f895))
 - fix(autocompact): restore manager state after fork to prevent name mutation ([#794](https://github.com/gptme/gptme/issues/794)) ([`ce25affc`](https://github.com/gptme/gptme/commit/ce25affc))
 - fix(autocompact): resolve NameError and naming bug ([#792](https://github.com/gptme/gptme/issues/792)) ([`16032e48`](https://github.com/gptme/gptme/commit/16032e48))
 - fix: dont mistake absolute path for command when given as prompt ([`55c5a92d`](https://github.com/gptme/gptme/commit/55c5a92d))
 - fix(server): support query param token for SSE authentication ([#785](https://github.com/gptme/gptme/issues/785)) ([`66b418a5`](https://github.com/gptme/gptme/commit/66b418a5))
 - fix: convert GEPA output_dir to absolute path to prevent FileNotFoundError ([#784](https://github.com/gptme/gptme/issues/784)) ([`ad5f47a1`](https://github.com/gptme/gptme/commit/ad5f47a1))
 - fix(shell): handle logical OR operators (||) in pipe detection ([#777](https://github.com/gptme/gptme/issues/777)) ([`d52a1729`](https://github.com/gptme/gptme/commit/d52a1729))
 - fix(anthropic): retry on RemoteProtocolError's ([#773](https://github.com/gptme/gptme/issues/773)) ([`9c7b4874`](https://github.com/gptme/gptme/commit/9c7b4874))
 - fix(shell): denylist pkill and killall (fixes [#768](https://github.com/gptme/gptme/issues/768)) ([#770](https://github.com/gptme/gptme/issues/770)) ([`6c793939`](https://github.com/gptme/gptme/commit/6c793939))

</p>
</details>

### 🔨 Misc (12)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.30.0 ([`bc4d8cd2`](https://github.com/gptme/gptme/commit/bc4d8cd2))
 - tests: add xfail to flaky test ([`0d9448af`](https://github.com/gptme/gptme/commit/0d9448af))
 - tests: add xfail to flaky test ([`2db3c003`](https://github.com/gptme/gptme/commit/2db3c003))
 - tests: fixed flaky tests ([`5eb817bd`](https://github.com/gptme/gptme/commit/5eb817bd))
 - refactor: extract cwd tracking to hook and refactor time/token awareness to clean hooks ([#839](https://github.com/gptme/gptme/issues/839)) ([`32dc01f6`](https://github.com/gptme/gptme/commit/32dc01f6))
 - Refactor threading.local to ContextVars support ([#827](https://github.com/gptme/gptme/issues/827)) ([`cb157ea1`](https://github.com/gptme/gptme/commit/cb157ea1))
 - refactor: migrate TTS to hook-based architecture ([#816](https://github.com/gptme/gptme/issues/816)) ([`83cbe9ff`](https://github.com/gptme/gptme/commit/83cbe9ff))
 - chore: change default TTS server port from 8000 to 8765 ([#815](https://github.com/gptme/gptme/issues/815)) ([`48f11f2c`](https://github.com/gptme/gptme/commit/48f11f2c))
 - refactor: move len_tokens and related code into gptme.util.tokens ([#809](https://github.com/gptme/gptme/issues/809)) ([`62ba557b`](https://github.com/gptme/gptme/commit/62ba557b))
 - perf: improve startup time by using `shutil.which` to check for pre-commit instead of `pre-commit --version` ([`630c0cc0`](https://github.com/gptme/gptme/commit/630c0cc0))
 - docs: add comprehensive lesson system documentation ([#771](https://github.com/gptme/gptme/issues/771)) ([`5671da68`](https://github.com/gptme/gptme/commit/5671da68))
 - docs(lessons): add Phase 6 comprehensive documentation ([#795](https://github.com/gptme/gptme/issues/795)) ([`8e11c24f`](https://github.com/gptme/gptme/commit/8e11c24f))

</p>
</details>

*(excluded 7 less relevant [commits](https://github.com/gptme/gptme/compare/v0.29.0...v0.30.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.29.0...v0.30.0
