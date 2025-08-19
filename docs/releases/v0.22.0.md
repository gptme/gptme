# v0.22.0

These are the release notes for gptme version v0.22.0.

## Contributors

Thanks to everyone who contributed to this release:

@0xbrayo, @erikbjare, @jrmi

## Changelog

Changes since v0.21.0:


## 📦 gptme

### ✨ Features (5)

 - feat: implement anthropic-style computer tool ([#225](https://github.com/gptme/gptme/issues/225)) ([`175167e0`](https://github.com/gptme/gptme/commit/175167e0))
 - feat: added /export comment to export self-contained HTML file built from webui ([#235](https://github.com/gptme/gptme/issues/235)) ([`0ca914fc`](https://github.com/gptme/gptme/commit/0ca914fc))
 - feat: add platform info to the system prompt ([#171](https://github.com/gptme/gptme/issues/171)) ([`0288d33a`](https://github.com/gptme/gptme/commit/0288d33a))
 - feat: added lynx browser support ([#214](https://github.com/gptme/gptme/issues/214)) ([`2cd9ffb8`](https://github.com/gptme/gptme/commit/2cd9ffb8))
 - feat: added deepseek support ([#180](https://github.com/gptme/gptme/issues/180)) ([`949eaeec`](https://github.com/gptme/gptme/commit/949eaeec))

### 🐛 Fixes (11)
<details><summary>Click to expand</summary>
<p>

 - fix: correct Linux distro version in systeminfo prompt ([#239](https://github.com/gptme/gptme/issues/239)) ([`3221145d`](https://github.com/gptme/gptme/commit/3221145d))
 - fix: informative message for agent when permission isn't granted ([#223](https://github.com/gptme/gptme/issues/223)) ([`5ee0f2f2`](https://github.com/gptme/gptme/commit/5ee0f2f2))
 - fix: fixed __version__ looking up old package name ([`f8c296f5`](https://github.com/gptme/gptme/commit/f8c296f5))
 - fix: set `<title>` in export and make sure `/export` command doesn't get saved to log ([`2a5576ad`](https://github.com/gptme/gptme/commit/2a5576ad))
 - fix: improved chat picker (now uses full-width of terminal) ([`32db19df`](https://github.com/gptme/gptme/commit/32db19df))
 - fix: add cli-provided prompts to readline history ([`ffaa109c`](https://github.com/gptme/gptme/commit/ffaa109c))
 - fix: improved reliability of llm.generate_name() ([`b9311036`](https://github.com/gptme/gptme/commit/b9311036))
 - fix: include conversation name in exported chat log ([`abc4928c`](https://github.com/gptme/gptme/commit/abc4928c))
 - fix: changed all Message-generator tools to simply print/return value instead (fixes [#186](https://github.com/gptme/gptme/issues/186), fixes [#187](https://github.com/gptme/gptme/issues/187)) ([`3dcef9fd`](https://github.com/gptme/gptme/commit/3dcef9fd))
 - fix: fixed support for groq (and deepseek?) ([#231](https://github.com/gptme/gptme/issues/231)) ([`cea30cfe`](https://github.com/gptme/gptme/commit/cea30cfe))
 - fix: fixed screenshot() not running if generator not consumed (such as when not last statement in codeblock) ([`65bdb8ad`](https://github.com/gptme/gptme/commit/65bdb8ad))

</p>
</details>

### 🔨 Misc (8)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.22.0 ([`cdd8c6b2`](https://github.com/gptme/gptme/commit/cdd8c6b2))
 - docs: removed bad computer use example ([`66898b23`](https://github.com/gptme/gptme/commit/66898b23))
 - tests: fixed blinking test due to changed working dir ([`40d1a5d7`](https://github.com/gptme/gptme/commit/40d1a5d7))
 - docs: add installing from source instructions to contributing guide ([#236](https://github.com/gptme/gptme/issues/236)) ([`4665f499`](https://github.com/gptme/gptme/commit/4665f499))
 - format: s/whitelist/allowlist ([`e77cd4ef`](https://github.com/gptme/gptme/commit/e77cd4ef))
 - docs: add mention of configuration file on providers page ([`16f8254e`](https://github.com/gptme/gptme/commit/16f8254e))
 - docs: fixed incorrect local/ollama/... provider prefix ([`3eb6f8f4`](https://github.com/gptme/gptme/commit/3eb6f8f4))
 - docs: improved note about outdated demos ([`cdce6373`](https://github.com/gptme/gptme/commit/cdce6373))

</p>
</details>

*(excluded 2 less relevant [commits](https://github.com/gptme/gptme/compare/v0.21.0...v0.22.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.21.0...v0.22.0
