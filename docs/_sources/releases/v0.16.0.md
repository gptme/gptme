# v0.16.0

These are the release notes for gptme version v0.16.0.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare

## Changelog

Changes since v0.15.0:


## 📦 gptme

### ✨ Features (7)

 - feat: basic support for openai/o1-preview and openai/o1-mini ([#117](https://github.com/gptme/gptme/issues/117)) ([`cf13bae8`](https://github.com/gptme/gptme/commit/cf13bae8))
 - feat: added youtube tool ([#116](https://github.com/gptme/gptme/issues/116)) ([`ad669920`](https://github.com/gptme/gptme/commit/ad669920))
 - feat: support placeholders in patches ([#114](https://github.com/gptme/gptme/issues/114)) ([`2e08a3a1`](https://github.com/gptme/gptme/commit/2e08a3a1))
 - feat: added read_chat function to chats tool ([#115](https://github.com/gptme/gptme/issues/115)) ([`527ce5ad`](https://github.com/gptme/gptme/commit/527ce5ad))
 - feat: added list_chats to chats tool, and cleaned up/refactored non-ToolSpec-using tools ([#110](https://github.com/gptme/gptme/issues/110)) ([`5cb3936d`](https://github.com/gptme/gptme/commit/5cb3936d))
 - feat: added a basic tool to search past conversation logs ([#109](https://github.com/gptme/gptme/issues/109)) ([`4e361093`](https://github.com/gptme/gptme/commit/4e361093))
 - feat: added terminal bell to alert the user they have been returned control ([`1da7d047`](https://github.com/gptme/gptme/commit/1da7d047))

### 🐛 Fixes (13)
<details><summary>Click to expand</summary>
<p>

 - fix: support multiple patches in a single codeblock ([#118](https://github.com/gptme/gptme/issues/118)) ([`ae3ea89b`](https://github.com/gptme/gptme/commit/ae3ea89b))
 - fix: made eval harness more reliable, using Manager ([#119](https://github.com/gptme/gptme/issues/119)) ([`0787f597`](https://github.com/gptme/gptme/commit/0787f597))
 - fix: remove spammy log message when youtube tool not available ([`6ab895c2`](https://github.com/gptme/gptme/commit/6ab895c2))
 - fix: extended the patch tool prompt to not strictly forbid placeholders, mention scoping strategies and fallback to save ([`7cf2119f`](https://github.com/gptme/gptme/commit/7cf2119f))
 - fix: added ./projects and ./demos to gitignore ([`ef7d9fd8`](https://github.com/gptme/gptme/commit/ef7d9fd8))
 - fix: change error on unknown codeblock langtags into warning, dont warn on empty langtag ([`481ab38e`](https://github.com/gptme/gptme/commit/481ab38e))
 - fix: minor improvements to --help output, updated --help example output in README ([`78f461ca`](https://github.com/gptme/gptme/commit/78f461ca))
 - fix: fixed excessive whitespace in patch example prompt ([`f0818c3d`](https://github.com/gptme/gptme/commit/f0818c3d))
 - fix: fixed formatting in tools/base.py ([`e3987bdd`](https://github.com/gptme/gptme/commit/e3987bdd))
 - fix: added gptme/server/__init__.py ([`02a3ecf4`](https://github.com/gptme/gptme/commit/02a3ecf4))
 - fix: improved how ToolUse examples are formatted ([`1e6574f1`](https://github.com/gptme/gptme/commit/1e6574f1))
 - fix: fixed bug in how examples were generated for patch tool ([`c8489ecc`](https://github.com/gptme/gptme/commit/c8489ecc))
 - fix: dont crash on unknown shell syntax ([`dd6fff7c`](https://github.com/gptme/gptme/commit/dd6fff7c))

</p>
</details>

### 🔨 Misc (5)

 - chore: bump version to 0.16.0 ([`d7f170cd`](https://github.com/gptme/gptme/commit/d7f170cd))
 - tests: bumped short token allowed token length ([`0208e76a`](https://github.com/gptme/gptme/commit/0208e76a))
 - refactor: refactor tools, codeblock, and tooluse ([#113](https://github.com/gptme/gptme/issues/113)) ([`0cad5ca7`](https://github.com/gptme/gptme/commit/0cad5ca7))
 - tests: added minimal tests for chats tool ([`d079af43`](https://github.com/gptme/gptme/commit/d079af43))
 - format: formatted codebase ([`740f1329`](https://github.com/gptme/gptme/commit/740f1329))

*(excluded 3 less relevant [commits](https://github.com/gptme/gptme/compare/v0.15.0...v0.16.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.15.0...v0.16.0
