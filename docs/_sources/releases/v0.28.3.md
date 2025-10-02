# v0.28.3

These are the release notes for gptme version v0.28.3.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare, @TimeToBuildBob

## Changelog

Changes since v0.28.2:


## 📦 gptme

### ✨ Features (2)

 - feat: improve save tool feedback with detailed status ([`d920ae5e`](https://github.com/gptme/gptme/commit/d920ae5e))
 - feat: add git safety guards to shell tool ([`fc78a042`](https://github.com/gptme/gptme/commit/fc78a042))

### 🐛 Fixes (7)
<details><summary>Click to expand</summary>
<p>

 - fix: update CI anthropic model to claude-3-5-haiku ([`28d9367c`](https://github.com/gptme/gptme/commit/28d9367c))
 - fix(shell): denylist should not trigger on content in quoted strings or heredocs ([`0ce788a2`](https://github.com/gptme/gptme/commit/0ce788a2))
 - fix(shell): improve denylist patterns for git commands ([`5e6bdc97`](https://github.com/gptme/gptme/commit/5e6bdc97))
 - fix: prevent premature code block closure during streaming with nested blocks ([#657](https://github.com/gptme/gptme/issues/657)) ([`55fedb76`](https://github.com/gptme/gptme/commit/55fedb76))
 - fix: change default/recommended model to Sonnet 4.5 ([`21de08ce`](https://github.com/gptme/gptme/commit/21de08ce))
 - fix: use dateutil.parser.isoparse for all datetime parsing ([`f6f3de5e`](https://github.com/gptme/gptme/commit/f6f3de5e))
 - fix: use dateutil.parser.isoparse instead of datetime.fromisoformat ([`934384dc`](https://github.com/gptme/gptme/commit/934384dc))

</p>
</details>

### 🔨 Misc (5)

 - chore: bump version to 0.28.3 ([`d22ff504`](https://github.com/gptme/gptme/commit/d22ff504))
 - docs: fixed v0.1.1 release notes ([`00ed0ece`](https://github.com/gptme/gptme/commit/00ed0ece))
 - docs: included all past changelogs ([`6d407047`](https://github.com/gptme/gptme/commit/6d407047))
 - docs: improve chat history prompt format ([`6fbd9db8`](https://github.com/gptme/gptme/commit/6fbd9db8))
 - docs: added release notes for v0.28.2 ([`481a07ff`](https://github.com/gptme/gptme/commit/481a07ff))

*(excluded 2 less relevant [commits](https://github.com/gptme/gptme/compare/v0.28.2...v0.28.3))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.28.2...v0.28.3
