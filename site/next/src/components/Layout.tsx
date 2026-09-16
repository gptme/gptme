import type { ReactNode } from "react";
import { cn, wrap } from "../lib/cn";
import { links } from "../lib/links";
import type { SiteStats } from "../lib/stats";
import { MenuIcon, StarIcon } from "./icons";

export type PageMeta = { title: string; description: string; path: string };

export function Head({ title, description, path }: PageMeta) {
  const canonical = new URL(path, "https://gptme.org").toString();
  return (
    <>
      <title>{title}</title>
      <meta name="description" content={description} />
      <link rel="canonical" href={canonical} />
      <meta property="og:title" content={title} />
      <meta property="og:description" content={description} />
      <meta property="og:image" content="https://gptme.org/media/logo.png" />
      <meta property="og:type" content="website" />
    </>
  );
}

const navLink = "flex items-center gap-[7px] text-ink-2 hover:text-accent-2-text max-md:js:py-3";

function Header({ stats }: { stats: SiteStats }) {
  return (
    <header className="relative border-b border-border">
      <div
        className={cn(
          wrap,
          "flex items-center justify-between gap-6 py-5 max-md:no-js:flex-wrap max-sm:py-[14px]",
        )}
      >
        <div className="flex items-center gap-3 max-sm:gap-[10px]">
          <a href="/" aria-label="gptme home" className="shrink-0">
            <img
              src="/media/icon.svg"
              alt=""
              width={34}
              height={34}
              className="h-[34px] w-[34px] max-sm:h-[30px] max-sm:w-[30px]"
            />
          </a>
          <div className="flex flex-col gap-px">
            <a
              href="/"
              className="font-mono text-xl font-semibold leading-[1.1] text-heading hover:text-heading max-sm:text-lg max-sm:leading-[1.1]"
            >
              gptme
            </a>
            <a
              href={links.superuserLabs}
              className="text-xs text-muted-text hover:text-accent-2-text max-sm:text-[11px]"
            >
              by Superuser Labs
            </a>
          </div>
        </div>

        <nav aria-label="Main">
          <button
            type="button"
            data-nav-toggle
            aria-expanded="false"
            aria-controls="nav-links"
            aria-label="Menu"
            className="hidden h-[42px] w-[42px] items-center justify-center rounded-md border border-border bg-surface text-heading max-md:js:inline-flex max-sm:h-11 max-sm:w-11 max-sm:rounded-[10px] max-sm:text-ink"
          >
            <MenuIcon />
          </button>
          <ul
            id="nav-links"
            className={cn(
              "m-0 flex list-none items-center gap-[30px] p-0 text-[15px] font-medium",
              "max-md:no-js:flex-wrap max-md:no-js:gap-x-5 max-md:no-js:gap-y-3",
              "max-md:js:hidden",
              "max-md:js:data-[open=true]:absolute max-md:js:data-[open=true]:inset-x-0 max-md:js:data-[open=true]:top-full max-md:js:data-[open=true]:z-20",
              "max-md:js:data-[open=true]:flex max-md:js:data-[open=true]:flex-col max-md:js:data-[open=true]:items-stretch max-md:js:data-[open=true]:gap-0",
              "max-md:js:data-[open=true]:border-b max-md:js:data-[open=true]:border-border max-md:js:data-[open=true]:bg-bg",
              "max-md:js:data-[open=true]:px-8 max-md:js:data-[open=true]:pb-5 max-md:js:data-[open=true]:pt-2 max-md:js:data-[open=true]:shadow-lg max-sm:js:data-[open=true]:px-5",
            )}
          >
            <li>
              <a className={navLink} href={links.docs}>
                Docs
              </a>
            </li>
            <li>
              <a className={navLink} href={links.demos}>
                Demos
              </a>
            </li>
            <li>
              <a className={navLink} href={links.stats}>
                Stats
              </a>
            </li>
            <li>
              <a className={navLink} href={links.timeline}>
                Timeline
              </a>
            </li>
            <li>
              <a className={navLink} href={links.github} aria-label={`GitHub, ${stats.starsLabel} stars`}>
                <StarIcon />
                <span>GitHub {stats.starsLabel}</span>
              </a>
            </li>
            <li>
              <a
                className="flex items-center gap-1.5 rounded-pill border border-border bg-surface px-4 py-2 text-ink hover:border-muted hover:text-ink max-md:js:mt-2 max-md:js:justify-center max-md:js:py-[10px]"
                href={links.gptmeAi}
              >
                <span>gptme.ai</span>
                <span className="font-normal text-muted-text">managed</span>
              </a>
            </li>
          </ul>
        </nav>
      </div>
    </header>
  );
}

const docsColumns: Array<[string, Array<[string, string]>]> = [
  [
    "Start",
    [
      ["Getting started", links.gettingStarted],
      ["Usage", links.usage],
      ["Examples", links.examples],
      ["Configuration", links.config],
      ["Tools", links.tools],
    ],
  ],
  [
    "Interfaces",
    [
      ["CLI", links.cli],
      ["Terminal UI", links.tui],
      ["Desktop & Android app", links.app],
      ["Web UI", links.webuiDocs],
      ["Server & REST API", links.server],
      ["Editor over ACP", links.acp],
      ["Channels: GitHub, email, chat, voice", links.channels],
    ],
  ],
  [
    "Build on it",
    [
      ["Agents", links.agents],
      ["Running agents autonomously", links.autonomous],
      ["Lessons", links.lessons],
      ["Skills", links.skills],
      ["Memory", links.memory],
      ["Plugins", links.plugins],
      ["Custom tools", links.customTool],
      ["Hooks", links.hooks],
      ["MCP", links.mcp],
    ],
  ],
  [
    "Reference",
    [
      ["Models", links.models],
      ["Providers", links.providers],
      ["Security", links.security],
      ["gptme.ai cloud", links.cloudDocs],
      ["Alternatives", links.alternatives],
      ["Timeline", links.timeline],
      ["Changelog", links.changelog],
    ],
  ],
];

function DocsIndex() {
  return (
    <section className="border-t border-border" aria-labelledby="docs-index-title">
      <div className={cn(wrap, "py-14 max-md:py-11 max-sm:py-9")}>
        <div className="mb-8 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 max-sm:mb-6 max-sm:flex-col max-sm:items-start max-sm:gap-3">
          <h2
            id="docs-index-title"
            className="m-0 text-[26px] font-semibold leading-[1.25] tracking-heading text-heading max-sm:text-[21px]"
          >
            Documentation
          </h2>
          <a href={links.docs} className="text-base font-medium text-accent hover:text-accent-2-text max-sm:text-[15px]">
            All docs →
          </a>
        </div>
        <div className="grid grid-cols-4 gap-x-8 gap-y-9 max-md:grid-cols-2 max-sm:grid-cols-1 max-sm:gap-y-7">
          {docsColumns.map(([heading, items]) => (
            <div key={heading} className="flex min-w-0 flex-col gap-3 max-sm:gap-2.5">
              <h3 className="m-0 font-mono text-[13px] font-medium uppercase tracking-eyebrow text-muted-text">
                {heading}
              </h3>
              <ul className="m-0 flex list-none flex-col gap-2.5 p-0 max-sm:gap-2">
                {items.map(([label, href]) => (
                  <li key={label}>
                    <a href={href} className="text-[15px] leading-[1.45] text-ink-2 hover:text-accent-2-text">
                      {label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function SuperuserBand() {
  return (
    <section className="bg-su-bg" aria-label="About Superuser Labs">
      <div
        className={cn(
          wrap,
          "flex items-center justify-between gap-8 py-9 max-md:flex-col max-md:items-start max-md:gap-5 max-sm:gap-[14px] max-sm:py-7",
        )}
      >
        <div className="flex items-center gap-5 max-sm:grid max-sm:grid-cols-[auto_minmax(0,1fr)] max-sm:gap-[14px]">
          <img
            src="/su-logo.png"
            alt="Superuser Labs logo"
            width={56}
            height={56}
            className="h-14 w-14 shrink-0 rounded-[10px] max-sm:h-12 max-sm:w-12 max-sm:rounded-md"
          />
          <div className="flex flex-col gap-1 max-sm:contents">
            <p className="m-0 text-lg font-semibold text-su-text max-sm:text-[17px]">Built by Superuser Labs</p>
            <p className="m-0 text-[15px] leading-[1.5] text-su-text-2 max-sm:col-span-full max-sm:text-sm max-sm:leading-[1.55]">
              A small open-source team from Sweden, building open-source tools since 2017. Also behind ActivityWatch.
            </p>
          </div>
        </div>
        <a
          href={links.superuserLabs}
          className="whitespace-nowrap font-mono text-[15px] text-su-link hover:text-su-text focus-visible:outline-su-link max-sm:text-sm"
        >
          superuserlabs.org →
        </a>
      </div>
    </section>
  );
}

const footerLinks: Array<[string, string]> = [
  ["Docs", links.docs],
  ["README", "/readme/"],
  ["GitHub", links.github],
  ["Discord", links.discord],
  ["gptme.ai", links.gptmeAi],
  ["superuserlabs.org", links.superuserLabs],
];

function Footer() {
  return (
    <footer>
      <div
        className={cn(
          wrap,
          "flex flex-wrap items-center justify-between gap-x-8 gap-y-4 py-[26px] text-sm text-muted-text max-md:flex-col max-md:items-start max-sm:gap-[14px] max-sm:pb-8 max-sm:pt-6 max-sm:text-[13px]",
        )}
      >
        <span>gptme · MIT · © Superuser Labs Lund AB</span>
        <ul className="m-0 flex list-none flex-wrap gap-x-[26px] gap-y-3 p-0 max-sm:order-first max-sm:gap-x-5 max-sm:gap-y-[10px]">
          {footerLinks.map(([label, href]) => (
            <li key={label}>
              <a href={href} className="text-ink-2 hover:text-accent-2-text">
                {label}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </footer>
  );
}

export function Layout({ stats, children }: { stats: SiteStats; children: ReactNode }) {
  return (
    <>
      <a
        href="#main"
        className="absolute -top-[100px] left-4 z-[100] rounded-md border border-border bg-surface px-4 py-2 text-ink focus:top-4"
      >
        Skip to content
      </a>
      <Header stats={stats} />
      <main id="main">{children}</main>
      <DocsIndex />
      <SuperuserBand />
      <Footer />
    </>
  );
}
