# gptme.org (next)

**Status: production landing page.** `make site` prerenders this tree, overlays
Sphinx docs / downloads / media / CNAME, and `.github/workflows/docs.yml`
publishes the result to GitHub Pages (gptme.org). The old pandoc homepage
(`site/template.html`) is no longer part of the deploy. Same-repo PRs still
get a Cloudflare Pages preview from `.github/workflows/site-next.yml`.

This directory is the redesigned gptme.org open-source showcase (design v3).
It has three pages:

- `/` is the landing page: hero with install command, "See it work" terminal card, the
  six ways to run gptme, project stats, Bob and the open-source/managed comparison,
  and a Superuser Labs band.
- `/readme/` renders the repository `README.md` at build time (GFM, GitHub-style
  heading anchors, without the logo/badge header). The HTML is sanitized before
  it is inserted into the page.
- `/stats/` shows usage and community stats (stars, PyPI and release downloads,
  repository activity) from [gptme/stats](https://github.com/gptme/stats).

## Stack

The stack matches `webui/`, so components and tokens can be shared later:

- Vite 8, React 19, TypeScript 7
- Tailwind CSS 4 (CSS-first config, `@tailwindcss/vite`) with `@tailwindcss/typography`

Pages are **prerendered to static HTML** for SEO, not shipped as a client-side SPA:

1. `vite build` builds the client assets and the `index.html` template. The client entry
   (`src/client.ts`) is only the CSS plus two small enhancements, the copy button and the
   mobile menu. There is no React hydration, so no React code runs in the browser.
2. `vite build --ssr src/entry-server.tsx` builds the server renderer.
3. `scripts/prerender.mjs` renders each route with `react-dom/server` into
   `dist/index.html`, `dist/readme/index.html` and `dist/stats/index.html`. It fails the
   build if the hero or stats content is missing.

In dev, a small Vite middleware (`vite.config.ts`) renders pages through the same server
entry.

## Run it

Requires Node.js 22.12+.

```sh
cd site/next
npm ci
npm run dev        # http://localhost:5173
npm run build      # static output in dist/
npm run preview    # serve dist/
npm run typecheck
```

Or from the repo root: `make site-next`.

The `dev` and `build` scripts first copy `media/logo.png` and `media/icon.svg` from the
repo root into `public/media/` (gitignored), so the site uses the same brand assets as
the README and docs.

## Data

Stats are fetched at build time from [gptme/stats](https://github.com/gptme/stats)
(`data/summary.json`). If the fetch fails (offline, rate limited), the build logs a warning
and uses the committed defaults in `src/lib/stats.ts`, so it never fails because of the
network.

The `/stats/` charts are the SVGs gptme/stats publishes under `charts/`, embedded from
`raw.githubusercontent.com`. The chart list is in `src/routes/Stats.tsx`; the build checks
each one and leaves out charts gptme/stats doesn't publish (yet), so a new chart can be
listed before it exists. `docs.yml` rebuilds gptme.org daily at 05:07 UTC, after the stats
collection run, so the numbers stay current without committing data to this repo.

## Design tokens

The design language lives in two files meant for reuse by the docs theme, `webui/` and
gptme.ai:

- `src/styles/tokens.css` defines the CSS custom properties: colors (`--color-*`), the
  terminal palette (`--term-*`), the Superuser Labs band (`--su-*`), type, radii and
  spacing. Light is the default (`:root`). Dark applies via `prefers-color-scheme: dark`,
  or explicitly with `data-theme="dark"` on `<html>`; `data-theme="light"` forces light.
- `src/styles/theme.css` maps those variables into the Tailwind theme (`@theme inline`).
  Components use classes like `bg-surface text-ink border-border text-accent-2-text
  bg-term-bg`, never raw hex values. Because the values are CSS variables, no `dark:`
  variants are needed. Another project adopts the tokens by importing `tokens.css` and
  `theme.css` after `@import "tailwindcss"`.

Two text-only variants exist for WCAG AA contrast on the light background:
`accent-2-text` (darker copper) and `muted-text`. See the header comment in `tokens.css`.

Fonts are self-hosted through `@fontsource` (Inter and IBM Plex Mono), with no Google
Fonts requests.

## Responsive

Breakpoints come from the design: `max-sm` (≤600px, following the MainMobile board),
`max-md` (≤900px, stacked layout) and `max-lg` (≤1100px). Components are written
desktop-first with those variants (see `src/styles/theme.css`).

## Layout

```
index.html               HTML template (<!--app-head--> / <!--app-html--> markers)
vite.config.ts           dev SSR middleware
scripts/prerender.mjs    static HTML for every route
src/
  client.ts              CSS + copy button + mobile menu (no hydration)
  entry-server.tsx       routes and render()
  components/Layout.tsx  head tags, header/nav, Superuser Labs band, footer
  components/icons.tsx   inline SVG icons
  routes/Home.tsx        landing page
  routes/Readme.tsx      rendered README
  routes/Stats.tsx       /stats/ page and its chart list
  lib/links.ts           outbound links (one place to update for docs.gptme.org)
  lib/stats.ts           build-time stats with fallback
  lib/readme.ts          README rendering
  styles/tokens.css      shared design tokens
  styles/theme.css       tokens -> Tailwind theme (shareable)
  styles/index.css       fonts, tokens, Tailwind, prose theme
```

## Not in scope

The docs.gptme.org move and sharing these tokens with `webui/` are separate steps.
The pandoc template at `site/template.html` is unused and can be deleted after soak.
