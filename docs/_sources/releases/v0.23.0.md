# v0.23.0

These are the release notes for gptme version v0.23.0.

## Contributors

Thanks to everyone who contributed to this release:

@0xbrayo, @erikbjare, @jrmi

## Changelog

Changes since v0.22.0:


## 📦 gptme

### ✨ Features (6)

 - feat: improved `ask_execute` options interface, now supporting '?' and 'auto' ([`66c977c0`](https://github.com/gptme/gptme/commit/66c977c0))
 - feat: copy to clipboard ([#234](https://github.com/gptme/gptme/issues/234)) ([`7ad4c947`](https://github.com/gptme/gptme/commit/7ad4c947))
 - feat(server): support interrupting stream, support slash commands ([#248](https://github.com/gptme/gptme/issues/248)) ([`c1d136f1`](https://github.com/gptme/gptme/commit/c1d136f1))
 - feat: added support for streaming in API ([#247](https://github.com/gptme/gptme/issues/247)) ([`7b622016`](https://github.com/gptme/gptme/commit/7b622016))
 - feat: added --cors-origin cli param to gptme-server ([`a7190edc`](https://github.com/gptme/gptme/commit/a7190edc))
 - feat: implemented gptme.vim plugin ([#241](https://github.com/gptme/gptme/issues/241)) ([`a04ee054`](https://github.com/gptme/gptme/commit/a04ee054))

### 🐛 Fixes (11)
<details><summary>Click to expand</summary>
<p>

 - fix: prompt to reinforce correct placement of filename parameter in patch tool ([#252](https://github.com/gptme/gptme/issues/252)) ([`01541af0`](https://github.com/gptme/gptme/commit/01541af0))
 - fix: trigger tool detection only on complete lines, for performance and to fix nested codeblocks in tooluse ([#251](https://github.com/gptme/gptme/issues/251)) ([`5a228d4e`](https://github.com/gptme/gptme/commit/5a228d4e))
 - fix: fixed prompt for python tool ([`64f94916`](https://github.com/gptme/gptme/commit/64f94916))
 - fix: fixes to computer use ([`1de06c67`](https://github.com/gptme/gptme/commit/1de06c67))
 - fix: switch to new Claude 3.5 models in evals, improve detection of results folders ([`6aec2313`](https://github.com/gptme/gptme/commit/6aec2313))
 - fix: switch to duckduckgo as default search engine with lynx browser ([#237](https://github.com/gptme/gptme/issues/237)) ([`ce7afb8f`](https://github.com/gptme/gptme/commit/ce7afb8f))
 - fix: fixed running evals in docker ([`5ac3914b`](https://github.com/gptme/gptme/commit/5ac3914b))
 - fix: added metadata for new haiku model ([`85647562`](https://github.com/gptme/gptme/commit/85647562))
 - fix: fix including files specified in gptme.toml ([`381dbc8f`](https://github.com/gptme/gptme/commit/381dbc8f))
 - fix: updated READMEs about gptme.vim ([`f7913bbc`](https://github.com/gptme/gptme/commit/f7913bbc))
 - fix: removed debug logging in gptme.vim ([`25c998c4`](https://github.com/gptme/gptme/commit/25c998c4))

</p>
</details>

### 🔨 Misc (12)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.23.0 ([`9010fede`](https://github.com/gptme/gptme/commit/9010fede))
 - chore: committed caches for contributor info used in changelog generation ([`9dee5548`](https://github.com/gptme/gptme/commit/9dee5548))
 - docs: improved server/computer use docs ([`501008a3`](https://github.com/gptme/gptme/commit/501008a3))
 - refactor: move get_workspace_prompt into prompts.py, add TODO comments ([`639e4cb1`](https://github.com/gptme/gptme/commit/639e4cb1))
 - docs: updated `gptme --help` output, fix command ordering ([`da90e33c`](https://github.com/gptme/gptme/commit/da90e33c))
 - docs: minor improvements to server docs ([`f6589de1`](https://github.com/gptme/gptme/commit/f6589de1))
 - docs: updated server docs with better structure and mention of gptme-webui ([`c32f64db`](https://github.com/gptme/gptme/commit/c32f64db))
 - docs(README): moved the use cases section above the developer/in-progress sections ([`0c0163d0`](https://github.com/gptme/gptme/commit/0c0163d0))
 - docs(README): rewrote use cases section ([`a517d8f3`](https://github.com/gptme/gptme/commit/a517d8f3))
 - docs: add link to SWE-Bench PR ([`970fc47e`](https://github.com/gptme/gptme/commit/970fc47e))
 - docs: improved arewetiny intro text ([`3f5aa840`](https://github.com/gptme/gptme/commit/3f5aa840))
 - docs: fixed gptme.vim docs ([`9ca2e083`](https://github.com/gptme/gptme/commit/9ca2e083))

</p>
</details>

*(excluded 3 less relevant [commits](https://github.com/gptme/gptme/compare/v0.22.0...v0.23.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.22.0...v0.23.0
