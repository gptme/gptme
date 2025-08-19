# v0.25.0

These are the release notes for gptme version v0.25.0.

## Contributors

Thanks to everyone who contributed to this release:

@0xbrayo, @bjsi, @erikbjare

## Changelog

Changes since v0.24.1:


## 📦 gptme

### ✨ Features (8)

 - feat: support `auto n` to skip n confirmations ([#346](https://github.com/gptme/gptme/issues/346)) ([`7d2e9e7b`](https://github.com/gptme/gptme/commit/7d2e9e7b))
 - feat: migrate readline to prompt_toolkit, with many features and fixes ([#244](https://github.com/gptme/gptme/issues/244)) ([`61ca0a24`](https://github.com/gptme/gptme/commit/61ca0a24))
 - feat(llm): add model metadata for Deepseek, Groq, and XAI ([`d2109fef`](https://github.com/gptme/gptme/commit/d2109fef))
 - feat: simplify rag tool by simply calling gptme-rag via subprocess ([#316](https://github.com/gptme/gptme/issues/316)) ([`87571086`](https://github.com/gptme/gptme/commit/87571086))
 - feat(anthropic): improve prompt caching and type safety ([#317](https://github.com/gptme/gptme/issues/317)) ([`a2f06df8`](https://github.com/gptme/gptme/commit/a2f06df8))
 - feat: started working on always-fresh context message ([#281](https://github.com/gptme/gptme/issues/281)) ([`01d80524`](https://github.com/gptme/gptme/commit/01d80524))
 - feat: support for OpenAI/Anthropic `tools` APIs ([#300](https://github.com/gptme/gptme/issues/300)) ([`d83f7238`](https://github.com/gptme/gptme/commit/d83f7238))
 - feat: output costs for request and session ([#293](https://github.com/gptme/gptme/issues/293)) ([`063624fe`](https://github.com/gptme/gptme/commit/063624fe))

### 🐛 Fixes (34)
<details><summary>Click to expand</summary>
<p>

 - fix: return instructions to correctly `playwright install` if browser binaries unavailable ([#358](https://github.com/gptme/gptme/issues/358)) ([`ebf56754`](https://github.com/gptme/gptme/commit/ebf56754))
 - fix: dont log costs by default ([`f65e75e6`](https://github.com/gptme/gptme/commit/f65e75e6))
 - fix: stream and capture ipython output ([#357](https://github.com/gptme/gptme/issues/357)) ([`ef424bed`](https://github.com/gptme/gptme/commit/ef424bed))
 - fix: removed spammy debug-logging message ([`99912f3c`](https://github.com/gptme/gptme/commit/99912f3c))
 - fix: properly handle piped input in prompts ([#354](https://github.com/gptme/gptme/issues/354)) ([`f8321834`](https://github.com/gptme/gptme/commit/f8321834))
 - fix: support piping stdin into gptme -r (resume) ([#344](https://github.com/gptme/gptme/issues/344)) ([`621ffd80`](https://github.com/gptme/gptme/commit/621ffd80))
 - fix: move playwright to thread to avoid event loop conflicts ([#353](https://github.com/gptme/gptme/issues/353)) ([`803d6e51`](https://github.com/gptme/gptme/commit/803d6e51))
 - fix: migrated to latest anthropic version v0.42 (prompt caching now stable) ([#352](https://github.com/gptme/gptme/issues/352)) ([`0ecf0459`](https://github.com/gptme/gptme/commit/0ecf0459))
 - fix: fix reading stdin after prompt-toolkit migration ([#337](https://github.com/gptme/gptme/issues/337)) ([`b3974c15`](https://github.com/gptme/gptme/commit/b3974c15))
 - fix: fix log output during input, fix directory completion ([#338](https://github.com/gptme/gptme/issues/338)) ([`e3874aa4`](https://github.com/gptme/gptme/commit/e3874aa4))
 - fix: remove prompting to avoid the now-supported EOF/HereDoc ([`3950a6e7`](https://github.com/gptme/gptme/commit/3950a6e7))
 - fix: support heredoc/EOF syntax in shell tool ([#335](https://github.com/gptme/gptme/issues/335)) ([`6a1246f5`](https://github.com/gptme/gptme/commit/6a1246f5))
 - fix: improved path detection in prompts, now works with more adjacent punctuation types ([#333](https://github.com/gptme/gptme/issues/333)) ([`8e8f28ea`](https://github.com/gptme/gptme/commit/8e8f28ea))
 - fix: use full paths to files in workspace context prompt ([#330](https://github.com/gptme/gptme/issues/330)) ([`a61aa284`](https://github.com/gptme/gptme/commit/a61aa284))
 - fix: fixed incorrectly always using gpt-4 model metadata when reducing ([`2343ab67`](https://github.com/gptme/gptme/commit/2343ab67))
 - fix: when patch failed due to bad path, include pwd in message for fast recovery ([`202869e6`](https://github.com/gptme/gptme/commit/202869e6))
 - fix(llm): transform message content for Groq provider ([`84c83166`](https://github.com/gptme/gptme/commit/84c83166))
 - fix: better prompt caching & less debug logging ([#323](https://github.com/gptme/gptme/issues/323)) ([`fae65ca4`](https://github.com/gptme/gptme/commit/fae65ca4))
 - fix: better anthropic cost logging ([#321](https://github.com/gptme/gptme/issues/321)) ([`283e8b0f`](https://github.com/gptme/gptme/commit/283e8b0f))
 - fix: better error output when loading locked conversation ([#319](https://github.com/gptme/gptme/issues/319)) ([`2264cf10`](https://github.com/gptme/gptme/commit/2264cf10))
 - fix: further anthropic caching improvements ([#318](https://github.com/gptme/gptme/issues/318)) ([`65efc3e0`](https://github.com/gptme/gptme/commit/65efc3e0))
 - fix: fixed regression in syntax highlighting for saves after refactor ([`a40f5387`](https://github.com/gptme/gptme/commit/a40f5387))
 - fix: added conversation lock file management in LogManager ([#295](https://github.com/gptme/gptme/issues/295)) ([`7255f255`](https://github.com/gptme/gptme/commit/7255f255))
 - fix: improvements after execute_with_confirmation refactor ([#311](https://github.com/gptme/gptme/issues/311)) ([`23f81cf8`](https://github.com/gptme/gptme/commit/23f81cf8))
 - fix: set TOKENIZERS_PARALLELISM env var to false to get rid of warning ([`75b298be`](https://github.com/gptme/gptme/commit/75b298be))
 - fix: clarify return prompt for subagent tool ([`29bcc473`](https://github.com/gptme/gptme/commit/29bcc473))
 - fix: skip loading subagent tool by default ([`37ae9c19`](https://github.com/gptme/gptme/commit/37ae9c19))
 - fix(prompts): minor fixes and improvements to prompts ([`10108a10`](https://github.com/gptme/gptme/commit/10108a10))
 - fix: fixed Dockerfile for computer use ([`9bf733a5`](https://github.com/gptme/gptme/commit/9bf733a5))
 - fix: include mention of user-edited save/patch/command in system response ([`93e126ac`](https://github.com/gptme/gptme/commit/93e126ac))
 - fix: make the --help text for --model always use recommended models ([`2c872f36`](https://github.com/gptme/gptme/commit/2c872f36))
 - fix: handle exceptions if readline history fails to write ([`c60eb883`](https://github.com/gptme/gptme/commit/c60eb883))
 - fix: prevent command execution from triggering unnecessary response generation ([`7f66dbff`](https://github.com/gptme/gptme/commit/7f66dbff))
 - fix: incorrect response_preference key in config ([`748be0b6`](https://github.com/gptme/gptme/commit/748be0b6))

</p>
</details>

### 🔨 Misc (26)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.25.0 ([`c3edcbf9`](https://github.com/gptme/gptme/commit/c3edcbf9))
 - refactor: make /tokens command use log_costs function ([`1fd407c1`](https://github.com/gptme/gptme/commit/1fd407c1))
 - docs: improved docs for playwright install, added instructions for lynx browser backend ([`a9bd44fa`](https://github.com/gptme/gptme/commit/a9bd44fa))
 - docs: list api key env variables uniformly for all providers ([`d598ff36`](https://github.com/gptme/gptme/commit/d598ff36))
 - perf: optimize token counting with caching and explicit model param ([#325](https://github.com/gptme/gptme/issues/325)) ([`c05f9c69`](https://github.com/gptme/gptme/commit/c05f9c69))
 - docs(providers): simplify provider docs and update API key config ([`c86b47cf`](https://github.com/gptme/gptme/commit/c86b47cf))
 - refactor: refactored how tools are loaded, to enable loading external tools ([#313](https://github.com/gptme/gptme/issues/313)) ([`c0eb21fa`](https://github.com/gptme/gptme/commit/c0eb21fa))
 - refactor: moved more core files to util module ([#312](https://github.com/gptme/gptme/issues/312)) ([`0ee07cf6`](https://github.com/gptme/gptme/commit/0ee07cf6))
 - docs: moved usage rubrics from example.rst to usage.rst ([`4ff383aa`](https://github.com/gptme/gptme/commit/4ff383aa))
 - test: disable generate primes test for gpt-4o-mini (unreliable) ([`696e722c`](https://github.com/gptme/gptme/commit/696e722c))
 - test: disable subagent test for gpt-4o-mini (unreliable) ([`b34cee08`](https://github.com/gptme/gptme/commit/b34cee08))
 - docs: added alternatives.rst to docs index ([`83736d6e`](https://github.com/gptme/gptme/commit/83736d6e))
 - docs: renamed comparison.rst to alternatives.rst, reorganized it ([`9744d36b`](https://github.com/gptme/gptme/commit/9744d36b))
 - docs: added basic comparison docs page ([`fb148333`](https://github.com/gptme/gptme/commit/fb148333))
 - refactor(tools): introduce execute_with_confirmation helper ([`df5c9d6e`](https://github.com/gptme/gptme/commit/df5c9d6e))
 - refactor: Move utility functions into dedicated modules, make tool content editable ([`d01b943d`](https://github.com/gptme/gptme/commit/d01b943d))
 - docs(gh-bot): fix `uses:` for example workflow in non-gptme repos ([`4d4c2b44`](https://github.com/gptme/gptme/commit/4d4c2b44))
 - docs: add quotes for [extras] in pipx install commands ([`6e4e3319`](https://github.com/gptme/gptme/commit/6e4e3319))
 - test: attempt more reliable prompting for the subagent test ([`fe6730c9`](https://github.com/gptme/gptme/commit/fe6730c9))
 - test: fixed tool format test and improved prompting for chain test ([`a7038e46`](https://github.com/gptme/gptme/commit/a7038e46))
 - refactor: improve tools API and fix issues ([`1c739527`](https://github.com/gptme/gptme/commit/1c739527))
 - refactor: cleanup and improve tools API ([`5b755f3d`](https://github.com/gptme/gptme/commit/5b755f3d))
 - Revert "fix: prevent command execution from triggering unnecessary response generation" ([`6a3813be`](https://github.com/gptme/gptme/commit/6a3813be))
 - test: added placeholders/TODOs for future tests ([`a70fdc01`](https://github.com/gptme/gptme/commit/a70fdc01))
 - docs: added documentation on agents.rst ([`b48f1517`](https://github.com/gptme/gptme/commit/b48f1517))
 - chore: updated changelog_contributors.csv cache ([`88c715b9`](https://github.com/gptme/gptme/commit/88c715b9))

</p>
</details>

*(excluded 6 less relevant [commits](https://github.com/gptme/gptme/compare/v0.24.1...v0.25.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.24.1...v0.25.0
