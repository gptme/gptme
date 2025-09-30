# v0.12.0

These are the release notes for gptme version v0.12.0.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare

## Changelog

Changes since v0.11.1:


## 📦 gptme

### ✨ Features (3)

 - feat: added ToolSpec for all tools, added basic XML-callstyle for tools ([`40905b0e`](https://github.com/gptme/gptme/commit/40905b0e))
 - feat(wip): started work on subagent ([`78aa1734`](https://github.com/gptme/gptme/commit/78aa1734))
 - feat: improvements to browsing, including link-following ([`15bd0acb`](https://github.com/gptme/gptme/commit/15bd0acb))

### 🐛 Fixes (25)
<details><summary>Click to expand</summary>
<p>

 - fix: removed order-sensitivity for test ([`c435b30b`](https://github.com/gptme/gptme/commit/c435b30b))
 - fix: added support for gpt-4o and gpt-4o-mini ([`9d81b319`](https://github.com/gptme/gptme/commit/9d81b319))
 - fix: fixed issues with applying patches ([`120233c0`](https://github.com/gptme/gptme/commit/120233c0))
 - fix: fix loading of base tools when browser tool not available ([`74e4b189`](https://github.com/gptme/gptme/commit/74e4b189))
 - fix: fixed typing ([`4c955ffa`](https://github.com/gptme/gptme/commit/4c955ffa))
 - fix: disabled test for search with duckduckgo ([`1411c8d3`](https://github.com/gptme/gptme/commit/1411c8d3))
 - fix: simplified `is_supported_codeblock` ([`cf30c44f`](https://github.com/gptme/gptme/commit/cf30c44f))
 - fix: added debug logging for test ([`0f3c0654`](https://github.com/gptme/gptme/commit/0f3c0654))
 - fix: leftover change leading to test failure ([`a4042d27`](https://github.com/gptme/gptme/commit/a4042d27))
 - fix: disable stripping dates and common prefixes from stdout/stderr in shell tool ([`3803f33b`](https://github.com/gptme/gptme/commit/3803f33b))
 - fix: fixed escaping when serializing messages to toml ([`50e81598`](https://github.com/gptme/gptme/commit/50e81598))
 - fix: fixed model metadata (added gpt-4-turbo) ([`9d74bf5d`](https://github.com/gptme/gptme/commit/9d74bf5d))
 - fix(nit): fixed comment and unused call ([`f9441b7c`](https://github.com/gptme/gptme/commit/f9441b7c))
 - fix: construct prompt from ToolSpec ([`0c76b975`](https://github.com/gptme/gptme/commit/0c76b975))
 - fix: include a summary of function-tools registered in the Python REPL ([`0eb5d5a8`](https://github.com/gptme/gptme/commit/0eb5d5a8))
 - fix: renamed register_function_conditional to register_function_if ([`e256fc96`](https://github.com/gptme/gptme/commit/e256fc96))
 - fix: let other tools register functions in the Python tool REPL ([`f4688aef`](https://github.com/gptme/gptme/commit/f4688aef))
 - fix: fixed imports in test_eval.py ([`83a747c2`](https://github.com/gptme/gptme/commit/83a747c2))
 - fix: strip ANSI escape sequences in Python output ([`a41a3145`](https://github.com/gptme/gptme/commit/a41a3145))
 - fix: fixed bug where Python output is duplicated in result message ([`a8cc4ef4`](https://github.com/gptme/gptme/commit/a8cc4ef4))
 - fix: fixed bug where tool prompts wouldn't show on installs without browser extras ([`92d0f75c`](https://github.com/gptme/gptme/commit/92d0f75c))
 - fix: use browser tools by writing Python code, added stripping of data:image's ([`bd32bc7a`](https://github.com/gptme/gptme/commit/bd32bc7a))
 - fix: reraise exception if over `tries` ([`b3de8f90`](https://github.com/gptme/gptme/commit/b3de8f90))
 - fix: cleaned up code that is now run as test ([`99d3d3d0`](https://github.com/gptme/gptme/commit/99d3d3d0))
 - fix: restart shell on broken pipe, handle composite expressions ([#70](https://github.com/gptme/gptme/issues/70)) ([`8612ca2b`](https://github.com/gptme/gptme/commit/8612ca2b))

</p>
</details>

### 🔨 Misc (6)

 - test: fixed _shorten_stdout tests ([`edb82ded`](https://github.com/gptme/gptme/commit/edb82ded))
 - nit: renamed func loop -> step ([`cafede58`](https://github.com/gptme/gptme/commit/cafede58))
 - refactor: refactored eval/main.py into seperate files ([`3eb49f22`](https://github.com/gptme/gptme/commit/3eb49f22))
 - test: increase system prompt token allowance in tests ([`f1655f63`](https://github.com/gptme/gptme/commit/f1655f63))
 - test: marked evals, disable running by default ([`14097057`](https://github.com/gptme/gptme/commit/14097057))
 - docs: fixed link ([`d3238e36`](https://github.com/gptme/gptme/commit/d3238e36))

*(excluded 2 less relevant [commits](https://github.com/gptme/gptme/compare/v0.11.1...v0.12.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.11.1...v0.12.0
