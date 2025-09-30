# v0.20.0

These are the release notes for gptme version v0.20.0.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare

## Changelog

Changes since v0.19.2:


## 📦 gptme

### ✨ Features (1)

 - feat: updated webui style, with sidebar ([`bd44e5c3`](https://github.com/gptme/gptme/commit/bd44e5c3))

### 🐛 Fixes (10)
<details><summary>Click to expand</summary>
<p>

 - fix: use importlib.util.find_spec instead of attempting costly imports at startup, reducing startup time by ~1s ([`7b755202`](https://github.com/gptme/gptme/commit/7b755202))
 - fix(webui): put `<thinking>` tags into `<details>`, minor style improvements ([`04b3109a`](https://github.com/gptme/gptme/commit/04b3109a))
 - fix(webui): switch to marked over showdown, improved styling and misc fixes ([`0a868ba3`](https://github.com/gptme/gptme/commit/0a868ba3))
 - fix: catch exceptions when executing tools ([`9560660e`](https://github.com/gptme/gptme/commit/9560660e))
 - fix: handle bad patches better ([`771734c4`](https://github.com/gptme/gptme/commit/771734c4))
 - fix: limit image size, fixes exception for large images (fixes [#185](https://github.com/gptme/gptme/issues/185)) ([#188](https://github.com/gptme/gptme/issues/188)) ([`45cfbaca`](https://github.com/gptme/gptme/commit/45cfbaca))
 - fix: fixed incorrectly nested string in f-string ([`4aa3f2da`](https://github.com/gptme/gptme/commit/4aa3f2da))
 - fix: made shell tool more strict, now requires exact 'shell' langtag to run ([`578adcc9`](https://github.com/gptme/gptme/commit/578adcc9))
 - fix: use stdout/stderr as langtags in shell output, instead of as headings ([`c102806d`](https://github.com/gptme/gptme/commit/c102806d))
 - fix: log warning if allowlisted tool could not be found ([`f1864c75`](https://github.com/gptme/gptme/commit/f1864c75))

</p>
</details>

### 🔨 Misc (9)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.20.0 ([`5d0b138c`](https://github.com/gptme/gptme/commit/5d0b138c))
 - docs: added startup time to 'Are we tiny?' page ([`cde7937d`](https://github.com/gptme/gptme/commit/cde7937d))
 - refactor: extracted js part of webui into seperate file ([`919fe93a`](https://github.com/gptme/gptme/commit/919fe93a))
 - docs: improved styling of demos ([`018e6ff6`](https://github.com/gptme/gptme/commit/018e6ff6))
 - docs: more use of rubric::, use sphinxcontrib.asciinema to embed player ([`fdfc6589`](https://github.com/gptme/gptme/commit/fdfc6589))
 - docs: improved getting started, tool docs, and docstrings ([`5c827eb4`](https://github.com/gptme/gptme/commit/5c827eb4))
 - docs: updated docs for running with ollama (litellm no longer needed) ([`f2586024`](https://github.com/gptme/gptme/commit/f2586024))
 - docs: updated docs for running locally with ollama/litellm ([`fa59310f`](https://github.com/gptme/gptme/commit/fa59310f))
 - improve: enhance API key setup UX and error handling ([`7231aa96`](https://github.com/gptme/gptme/commit/7231aa96))

</p>
</details>

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.19.2...v0.20.0
