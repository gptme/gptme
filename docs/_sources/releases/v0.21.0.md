# v0.21.0

These are the release notes for gptme version v0.21.0.

## Contributors

Thanks to everyone who contributed to this release:

@0xbrayo, @erikbjare

## Changelog

Changes since v0.20.0:


## 📦 gptme

### ✨ Features (4)

 - feat: added support for groq provider ([`4299cd02`](https://github.com/gptme/gptme/commit/4299cd02))
 - feat:whitelist some commands ([`7f1ba2be`](https://github.com/gptme/gptme/commit/7f1ba2be))
 - feat: added support for xAI/grok ([`d7cebbae`](https://github.com/gptme/gptme/commit/d7cebbae))
 - feat: started working on ncurses ui ([`d3413eab`](https://github.com/gptme/gptme/commit/d3413eab))

### 🐛 Fixes (19)
<details><summary>Click to expand</summary>
<p>

 - fix: added 'head' to allowlisted commands in shell tool ([`7cc752f6`](https://github.com/gptme/gptme/commit/7cc752f6))
 - fix: compile cmd_regex ([#222](https://github.com/gptme/gptme/issues/222)) ([`623a52d1`](https://github.com/gptme/gptme/commit/623a52d1))
 - fix: print used model on startup ([`6cf4001d`](https://github.com/gptme/gptme/commit/6cf4001d))
 - fix: better error if attempting to run on Windows, refactor readline stuff ([#221](https://github.com/gptme/gptme/issues/221)) ([`bd8b746b`](https://github.com/gptme/gptme/commit/bd8b746b))
 - fix: update to use latest Sonnet model by default, improve typing ([`6e701686`](https://github.com/gptme/gptme/commit/6e701686))
 - fix: dont catch interrupts until conversation has begun ([`2be45a88`](https://github.com/gptme/gptme/commit/2be45a88))
 - fix: added ncgptme and gptme-nc script entrypoints for ncurses tui ([`7b19b760`](https://github.com/gptme/gptme/commit/7b19b760))
 - fix: fixed typing in ncurses.py ([`f018c6c1`](https://github.com/gptme/gptme/commit/f018c6c1))
 - fix: improved shell tool preview format ([`756e4207`](https://github.com/gptme/gptme/commit/756e4207))
 - fix: change OPENAI_API_BASE to OPENAI_BASE_URL ([`30e3f01e`](https://github.com/gptme/gptme/commit/30e3f01e))
 - fix: ensure subagent logdir unique ([`f735111e`](https://github.com/gptme/gptme/commit/f735111e))
 - fix: changed subagent tool param order and example ([`2bdb48fa`](https://github.com/gptme/gptme/commit/2bdb48fa))
 - fix: fixed display bug in confirm prompt ([`b9f8cd89`](https://github.com/gptme/gptme/commit/b9f8cd89))
 - fix: fixed bug where user prompt not included in request ([`53d160eb`](https://github.com/gptme/gptme/commit/53d160eb))
 - fix: fixed prompt_user returning empty string when interrupted ([`440aedb0`](https://github.com/gptme/gptme/commit/440aedb0))
 - fix: fixed leftover call to ask_execute instead of confirm func ([`b7d2a3fe`](https://github.com/gptme/gptme/commit/b7d2a3fe))
 - fix: fixed incorrectly asking for confirmation when impersonating ([`94983436`](https://github.com/gptme/gptme/commit/94983436))
 - fix: more fixes/improvements to treeofthoughts.py ([`d54df51d`](https://github.com/gptme/gptme/commit/d54df51d))
 - fix: re-raise tool use errors in tests ([`0ada191e`](https://github.com/gptme/gptme/commit/0ada191e))

</p>
</details>

### 🔨 Misc (14)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.21.0 ([`852e6f40`](https://github.com/gptme/gptme/commit/852e6f40))
 - docs: added more WIP features to README ([`2d8b6020`](https://github.com/gptme/gptme/commit/2d8b6020))
 - docs: added note about Windows support ([`b8ce0406`](https://github.com/gptme/gptme/commit/b8ce0406))
 - docs: added docstring to ncurses.py ([`5bb00249`](https://github.com/gptme/gptme/commit/5bb00249))
 - docs: add note about limitations of small local models, convert page to rst ([`f521c71b`](https://github.com/gptme/gptme/commit/f521c71b))
 - format: fixed lints ([`ff2277ca`](https://github.com/gptme/gptme/commit/ff2277ca))
 - refactor: refactor ncurses.py and add --no-color cli argument ([`942996c9`](https://github.com/gptme/gptme/commit/942996c9))
 - docs: added basic docs for configuration files (fixes [#173](https://github.com/gptme/gptme/issues/173)) ([`acd0ceee`](https://github.com/gptme/gptme/commit/acd0ceee))
 - docs(README): removed old ToC link ([`5801bbff`](https://github.com/gptme/gptme/commit/5801bbff))
 - docs: fixed docs building after refactor ([`52eaed9f`](https://github.com/gptme/gptme/commit/52eaed9f))
 - refactor: refactor how confirmation works, enabling LLM-guided confirmation and simplifying confirmation support in server ([`b843e889`](https://github.com/gptme/gptme/commit/b843e889))
 - refactor: work on programmatic interface, refactored LogManager into mutable manager and immutable Log dataclass, added wip treeofthought script ([`d421cc8c`](https://github.com/gptme/gptme/commit/d421cc8c))
 - format: fixed formatting after pre-commit setup ([`4ee9761a`](https://github.com/gptme/gptme/commit/4ee9761a))
 - docs: fixed incorrect OPENAI_API_BASE url ([`732c5b85`](https://github.com/gptme/gptme/commit/732c5b85))

</p>
</details>

*(excluded 4 less relevant [commits](https://github.com/gptme/gptme/compare/v0.20.0...v0.21.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.20.0...v0.21.0
