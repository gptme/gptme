# v0.24.0

These are the release notes for gptme version v0.24.0.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare, @jrmi

## Changelog

Changes since v0.23.0:


## 📦 gptme

### ✨ Features (5)

 - feat(cli): print return values from tool function calls ([`c1f4c005`](https://github.com/gptme/gptme/commit/c1f4c005))
 - feat(utils): add verbose logging and tools call command to gptme-util ([#265](https://github.com/gptme/gptme/issues/265)) ([`b3955d56`](https://github.com/gptme/gptme/commit/b3955d56))
 - feat: begin work on RAG, refactoring, improved docs and tests ([#258](https://github.com/gptme/gptme/issues/258)) ([`950c3972`](https://github.com/gptme/gptme/commit/950c3972))
 - feat: implement gptme-util CLI for utilities ([#261](https://github.com/gptme/gptme/issues/261)) ([`128676c0`](https://github.com/gptme/gptme/commit/128676c0))
 - feat: added current date & time to system prompt ([`faaec3a3`](https://github.com/gptme/gptme/commit/faaec3a3))

### 🐛 Fixes (5)

 - fix: support line removal in patch tool ([#267](https://github.com/gptme/gptme/issues/267)) ([`7c51573f`](https://github.com/gptme/gptme/commit/7c51573f))
 - fix: enforce full path usage in patch tool ([`2ade6677`](https://github.com/gptme/gptme/commit/2ade6677))
 - fix(save): fix save diff preview when path starts with ~/ ([`0fbafd07`](https://github.com/gptme/gptme/commit/0fbafd07))
 - fix: set PAGER and GH_PAGER in shell tool, add wait-for-ci example to gh tool ([`e4ce6c8d`](https://github.com/gptme/gptme/commit/e4ce6c8d))
 - fix: mention paths being relative to working dir in prompt for save tool ([`41594c57`](https://github.com/gptme/gptme/commit/41594c57))

### 🔨 Misc (8)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.24.0 ([`990965f1`](https://github.com/gptme/gptme/commit/990965f1))
 - test(patch): improve test coverage for patch tool ([`e1e2943b`](https://github.com/gptme/gptme/commit/e1e2943b))
 - test(patch): improve empty patch test ([`a6605127`](https://github.com/gptme/gptme/commit/a6605127))
 - refactor(rag): simplify configuration and initialization ([#266](https://github.com/gptme/gptme/issues/266)) ([`84e5ab63`](https://github.com/gptme/gptme/commit/84e5ab63))
 - refactor: group all provider/model related logic into `llm` directory ([#254](https://github.com/gptme/gptme/issues/254)) ([`ac3c1730`](https://github.com/gptme/gptme/commit/ac3c1730))
 - docs(README): added links to issues for agents and Bob ([`ebd700b7`](https://github.com/gptme/gptme/commit/ebd700b7))
 - chore: moved mypy options into pyproject.toml ([`ba2009aa`](https://github.com/gptme/gptme/commit/ba2009aa))
 - docs: moved a couple 'in progress' features to the completed features section ([`e9a00228`](https://github.com/gptme/gptme/commit/e9a00228))

</p>
</details>

*(excluded 2 less relevant [commits](https://github.com/gptme/gptme/compare/v0.23.0...v0.24.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.23.0...v0.24.0
