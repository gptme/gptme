/**
 * gptme Tailwind preset: maps the design tokens in src/styles/tokens.css into
 * the Tailwind theme, so components use classes like `bg-surface text-ink
 * border-border` instead of raw hex values.
 *
 * The values are CSS variables, so light/dark switching happens in tokens.css
 * (prefers-color-scheme, or data-theme on <html>) and no `dark:` variants are
 * needed. Other gptme surfaces (webui, docs theme, gptme.ai) can adopt the same
 * design language by importing tokens.css and adding this file to `presets`.
 */
import type { Config } from "tailwindcss";

const preset = {
  content: [],
  theme: {
    extend: {
      colors: {
        bg: "var(--color-bg)",
        surface: "var(--color-surface)",
        border: "var(--color-border)",
        chip: "var(--color-chip)",
        ink: "var(--color-ink)",
        heading: "var(--color-heading)",
        "ink-2": "var(--color-ink-2)",
        muted: {
          DEFAULT: "var(--color-muted)",
          // AA-safe variant for small text on light backgrounds
          text: "var(--color-muted-text)",
        },
        accent: "var(--color-accent)",
        "accent-2": {
          DEFAULT: "var(--color-accent-2)",
          // AA-safe variant for small text on light backgrounds
          text: "var(--color-accent-2-text)",
        },
        "on-accent": "var(--color-on-accent)",
        ring: "var(--color-ring)",
        term: {
          bg: "var(--term-bg)",
          border: "var(--term-border)",
          header: "var(--term-header)",
          text: "var(--term-text)",
          muted: "var(--term-muted)",
          tool: "var(--term-tool)",
          pass: "var(--term-pass)",
          fail: "var(--term-fail)",
          prompt: "var(--term-prompt)",
        },
        su: {
          bg: "var(--su-bg)",
          text: "var(--su-text)",
          "text-2": "var(--su-text-2)",
          link: "var(--su-link)",
        },
      },
      fontFamily: {
        sans: "var(--font-sans)",
        mono: "var(--font-mono)",
      },
      letterSpacing: {
        eyebrow: "var(--tracking-eyebrow)",
        heading: "var(--tracking-tight)",
        display: "var(--tracking-display)",
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
        pill: "var(--radius-pill)",
      },
      spacing: {
        18: "4.5rem",
        22: "5.5rem",
      },
      maxWidth: {
        page: "var(--page-max)",
      },
      boxShadow: {
        term: "var(--term-shadow)",
      },
      typography: {
        DEFAULT: {
          css: {
            "--tw-prose-body": "var(--color-ink)",
            "--tw-prose-headings": "var(--color-heading)",
            "--tw-prose-lead": "var(--color-ink-2)",
            "--tw-prose-links": "var(--color-accent)",
            "--tw-prose-bold": "var(--color-heading)",
            "--tw-prose-counters": "var(--color-muted-text)",
            "--tw-prose-bullets": "var(--color-muted)",
            "--tw-prose-hr": "var(--color-border)",
            "--tw-prose-quotes": "var(--color-ink-2)",
            "--tw-prose-quote-borders": "var(--color-accent-2)",
            "--tw-prose-captions": "var(--color-muted-text)",
            "--tw-prose-code": "var(--color-heading)",
            "--tw-prose-pre-code": "var(--term-text)",
            "--tw-prose-pre-bg": "var(--term-bg)",
            "--tw-prose-th-borders": "var(--color-border)",
            "--tw-prose-td-borders": "var(--color-border)",
            a: { textDecoration: "none", fontWeight: "500" },
            "a:hover": { color: "var(--color-accent-2-text)" },
            "code::before": { content: "none" },
            "code::after": { content: "none" },
            code: {
              fontFamily: "var(--font-mono)",
              fontWeight: "400",
              backgroundColor: "var(--color-chip)",
              borderRadius: "4px",
              padding: "0.1em 0.35em",
            },
            pre: { border: "1px solid var(--term-border)" },
            "pre code": { backgroundColor: "transparent", padding: "0" },
            // README callouts (> [!NOTE]) read badly with decorative quotes and italics.
            blockquote: { fontStyle: "normal", fontWeight: "400" },
            "blockquote p:first-of-type::before": { content: "none" },
            "blockquote p:last-of-type::after": { content: "none" },
            '[align="center"]': { textAlign: "center" },
            img: { display: "inline-block", marginTop: "0", marginBottom: "0" },
          },
        },
      },
    },
  },
} satisfies Config;

export default preset;
