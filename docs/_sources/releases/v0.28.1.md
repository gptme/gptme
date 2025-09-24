# v0.28.1

These are the release notes for gptme version v0.28.1.

## Contributors

Thanks to everyone who contributed to this release:

@delorenj, @erikbjare

## Changelog

Changes since v0.28.0:


## 📦 gptme

### ✨ Features (14)

 - feat: add denylist for dangerous shell commands with specific deny reasons ([#648](https://github.com/gptme/gptme/issues/648)) ([`5b977846`](https://github.com/gptme/gptme/commit/5b977846))
 - feat: implement get_model TODO and fix critical telemetry bug ([#647](https://github.com/gptme/gptme/issues/647)) ([`9c05ac26`](https://github.com/gptme/gptme/commit/9c05ac26))
 - feat: add trajectory-rich tasks for GEPA optimization ([#640](https://github.com/gptme/gptme/issues/640)) ([`962bdf3c`](https://github.com/gptme/gptme/commit/962bdf3c))
 - feat: add user-level files support in config ([#639](https://github.com/gptme/gptme/issues/639)) ([`fb3bbfb7`](https://github.com/gptme/gptme/commit/fb3bbfb7))
 - feat: add HTTP MCP Server Support + CLI Management Tools ([#635](https://github.com/gptme/gptme/issues/635)) ([`0a4ef349`](https://github.com/gptme/gptme/commit/0a4ef349))
 - feat: added cross-conversation context ([#636](https://github.com/gptme/gptme/issues/636)) ([`54f289ec`](https://github.com/gptme/gptme/commit/54f289ec))
 - feat: add cache-aware cost calculation, use in telemetry ([#631](https://github.com/gptme/gptme/issues/631)) ([`b3cd6e07`](https://github.com/gptme/gptme/commit/b3cd6e07))
 - feat: dspy experiment ([#627](https://github.com/gptme/gptme/issues/627)) ([`fb61ddda`](https://github.com/gptme/gptme/commit/fb61ddda))
 - feat: filter out resolved comments from GitHub PR content ([`d612a355`](https://github.com/gptme/gptme/commit/d612a355))
 - feat: add GitHub Actions status to PR content fetching ([`947700de`](https://github.com/gptme/gptme/commit/947700de))
 - feat: add auto-naming for conversations in server API ([#621](https://github.com/gptme/gptme/issues/621)) ([`cfce2bc8`](https://github.com/gptme/gptme/commit/cfce2bc8))
 - feat: add todo tools for conversation-scoped task management ([#622](https://github.com/gptme/gptme/issues/622)) ([`ce3bfe72`](https://github.com/gptme/gptme/commit/ce3bfe72))
 - feat: include pwd and agent workspace path in system prompt ([`b38e06d0`](https://github.com/gptme/gptme/commit/b38e06d0))
 - feat: add GitHub issue/PR link handling in context ([#619](https://github.com/gptme/gptme/issues/619)) ([`f79e2428`](https://github.com/gptme/gptme/commit/f79e2428))

### 🐛 Fixes (25)
<details><summary>Click to expand</summary>
<p>

 - fix(api): support auto-generating agent path from name ([#646](https://github.com/gptme/gptme/issues/646)) ([`62534a95`](https://github.com/gptme/gptme/commit/62534a95))
 - fix: propagate agent logdir from subprocess in DSPy evaluations ([#643](https://github.com/gptme/gptme/issues/643)) ([`d6c9fc69`](https://github.com/gptme/gptme/commit/d6c9fc69))
 - fix(eval): fix DSPy integration to use real evaluation specs ([#630](https://github.com/gptme/gptme/issues/630)) ([`fa48bb34`](https://github.com/gptme/gptme/commit/fa48bb34))
 - fix: added qwen3-max metadata ([`a471d1de`](https://github.com/gptme/gptme/commit/a471d1de))
 - fix: add initial support for magistral models ([`59dce119`](https://github.com/gptme/gptme/commit/59dce119))
 - fix: fixes on top of HTTP MCP server support ([#637](https://github.com/gptme/gptme/issues/637)) ([`21dc660a`](https://github.com/gptme/gptme/commit/21dc660a))
 - fix: switch to sonar-pro by default for perplexity ([`d9e99aef`](https://github.com/gptme/gptme/commit/d9e99aef))
 - fix: extract env vars to constants ([`0eb0c9b0`](https://github.com/gptme/gptme/commit/0eb0c9b0))
 - fix: improve todo tool, support writing multiple tasks in one tool call ([`61727b43`](https://github.com/gptme/gptme/commit/61727b43))
 - fix: correct optimizers parameter format in workflow ([`9c565269`](https://github.com/gptme/gptme/commit/9c565269))
 - fix: handle models that dont support vision, stricter openrouter provider selection, fixes to deepseek & kimi-k2 ([`1c18c3fa`](https://github.com/gptme/gptme/commit/1c18c3fa))
 - fix: added chime to generate_sounds script ([`fe24000f`](https://github.com/gptme/gptme/commit/fe24000f))
 - fix: misc fixes ([#626](https://github.com/gptme/gptme/issues/626)) ([`ff0b6c74`](https://github.com/gptme/gptme/commit/ff0b6c74))
 - fix: catch exception if image file cannot be read ([`8165857c`](https://github.com/gptme/gptme/commit/8165857c))
 - fix: update provider examples ([`faf56f60`](https://github.com/gptme/gptme/commit/faf56f60))
 - fix: move GitHub Actions status to end of PR content ([`174cc409`](https://github.com/gptme/gptme/commit/174cc409))
 - fix: make morph tool work with all tool formats (fixes [#603](https://github.com/gptme/gptme/issues/603)) ([`181a5fca`](https://github.com/gptme/gptme/commit/181a5fca))
 - fix: better tree output ([#624](https://github.com/gptme/gptme/issues/624)) ([`6782d81a`](https://github.com/gptme/gptme/commit/6782d81a))
 - fix: fixed todo tools ([`daf128aa`](https://github.com/gptme/gptme/commit/daf128aa))
 - fix: add browser resilience with auto-restart and retry mechanism ([`26c93942`](https://github.com/gptme/gptme/commit/26c93942))
 - fix: include starting working directory and use absolute paths in system prompt ([`204d09d2`](https://github.com/gptme/gptme/commit/204d09d2))
 - fix(server): fix setting initial working directory from workspace ([`03aa619e`](https://github.com/gptme/gptme/commit/03aa619e))
 - fix: use "Initial Working Directory" instead of "Current Directory" ([`c6cbc272`](https://github.com/gptme/gptme/commit/c6cbc272))
 - fix: exclude untracked files from autocommit status check ([`da5fb7f5`](https://github.com/gptme/gptme/commit/da5fb7f5))
 - fix: resolve audio blocking and ALSA/PulseAudio timeout issues ([#620](https://github.com/gptme/gptme/issues/620)) ([`d6703e85`](https://github.com/gptme/gptme/commit/d6703e85))

</p>
</details>

### 🔨 Misc (13)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.28.1 ([`dae71fc5`](https://github.com/gptme/gptme/commit/dae71fc5))
 - test: fix todo tool test expectations to match implementation ([`e9ff2efc`](https://github.com/gptme/gptme/commit/e9ff2efc))
 - docs: fixed incorrect example ([`1aaad3d0`](https://github.com/gptme/gptme/commit/1aaad3d0))
 - chore: comment out excessive debug logging ([`65f68e23`](https://github.com/gptme/gptme/commit/65f68e23))
 - refactor: improve ask_execute variable naming and imports ([`471ad1b5`](https://github.com/gptme/gptme/commit/471ad1b5))
 - docs: clarify gptme description and improve usage examples ([`f606ccd2`](https://github.com/gptme/gptme/commit/f606ccd2))
 - docs: fix outdated model reference ([`d7a06264`](https://github.com/gptme/gptme/commit/d7a06264))
 - chore: fix gitignore ([`8e262818`](https://github.com/gptme/gptme/commit/8e262818))
 - docs: fix docs for changelog/release notes ([#623](https://github.com/gptme/gptme/issues/623)) ([`8c6caf5f`](https://github.com/gptme/gptme/commit/8c6caf5f))
 - docs: create changelogs for tagged versions in ./docs/changelog using make target ([`4f69ef2e`](https://github.com/gptme/gptme/commit/4f69ef2e))
 - docs: update project description to emphasize modern agent capabilities ([`37a179a1`](https://github.com/gptme/gptme/commit/37a179a1))
 - Improve tree output and autocommit functionality ([`a6008fc2`](https://github.com/gptme/gptme/commit/a6008fc2))
 - chore: updated changelog_contributors.csv cache ([`f045f329`](https://github.com/gptme/gptme/commit/f045f329))

</p>
</details>

*(excluded 8 less relevant [commits](https://github.com/gptme/gptme/compare/v0.28.0...v0.28.1))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.28.0...v0.28.1
