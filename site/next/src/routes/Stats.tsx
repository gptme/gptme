import type { ReactNode } from "react";
import { cn, eyebrow, wrap } from "../lib/cn";
import { links } from "../lib/links";
import {
  formatDate,
  formatInt,
  type Chart,
  type ResolvedChart,
  type SiteStats,
  type StatsSummary,
} from "../lib/stats";

// Charts published by gptme/stats (charts/*.svg), grouped by page section.
// Charts that don't exist (yet) are dropped at build time by resolveCharts,
// so a chart can be listed here before gptme/stats starts publishing it.
export const statsCharts = {
  stars: [
    {
      file: "stars.svg",
      alt: "Cumulative GitHub stars of gptme/gptme over time",
      data: "stars.csv",
      established: true,
    },
    {
      // Added by gptme/stats#4; shows up once that is merged.
      file: "stars_growth.svg",
      alt: "GitHub stars added per day, 7-day rolling average",
      data: "daily.csv",
      established: false,
    },
  ],
  pypi: [
    {
      file: "pypi.svg",
      alt: "Daily PyPI downloads of the gptme package with a 30-day average",
      data: "pypi_daily.csv",
      established: true,
    },
  ],
  releases: [
    {
      file: "downloads.svg",
      alt: "Cumulative GitHub release asset downloads of gptme/gptme",
      data: "daily.csv",
      established: true,
    },
  ],
} satisfies Record<string, Chart[]>;

export type StatsChartGroup = keyof typeof statsCharts;
export type StatsCharts = Record<StatsChartGroup, ResolvedChart[]>;

type Tile = { label: string; value: number | null | undefined; note?: string };

const dataFile = (name: string) => `${links.stats}/blob/master/data/${name}`;

function Tiles({ items }: { items: Tile[] }) {
  const shown = items.filter((t) => typeof t.value === "number");
  if (shown.length === 0) return null;
  return (
    <dl className="m-0 grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3 max-sm:grid-cols-2 max-sm:gap-2">
      {shown.map((t) => (
        <div key={t.label} className="flex flex-col-reverse justify-end gap-1 rounded-lg border border-border bg-surface px-5 py-4 max-sm:px-4 max-sm:py-3">
          <dt className="text-sm leading-[1.4] text-muted-text max-sm:text-[13px]">
            {t.label}
            {t.note && <span className="block text-xs">{t.note}</span>}
          </dt>
          <dd className="m-0 font-mono text-[28px] font-semibold leading-[1.2] text-heading max-sm:text-[22px]">
            {formatInt(t.value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Charts({ charts }: { charts: ResolvedChart[] }) {
  if (charts.length === 0) return null;
  return (
    <div className={cn("grid gap-4", charts.length > 1 ? "grid-cols-2 max-lg:grid-cols-1" : "max-w-[900px]")}>
      {charts.map((c) => (
        <figure key={c.file} className="m-0 flex min-w-0 flex-col gap-2">
          <a href={c.dataHref} className="block overflow-hidden rounded-lg border border-border">
            {/* The SVGs are light cards (they are made for GitHub READMEs), in both themes. */}
            <img src={c.src} alt={c.alt} width={768} height={384} loading="lazy" className="block h-auto w-full" />
          </a>
          <figcaption className="text-[13px] text-muted-text">
            Data: <a href={c.dataHref}>{c.data}</a>
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-5 border-t border-border py-12 max-sm:gap-4 max-sm:py-9">
      <h2
        id={id}
        className="m-0 text-[26px] font-semibold leading-[1.25] tracking-heading text-heading max-sm:text-[21px]"
      >
        {title}
      </h2>
      {children}
    </section>
  );
}

const lede = "m-0 max-w-[760px] text-base leading-[1.6] text-ink-2 max-sm:text-[15px]";

export function Stats({ stats, charts }: { stats: SiteStats; charts: StatsCharts }) {
  const s: StatsSummary = stats.summary;
  const pypi = s.pypi;
  return (
    <div className={cn(wrap, "pb-16 max-sm:pb-10")}>
      <header className="flex flex-col gap-3 pb-10 pt-16 max-sm:pb-8 max-sm:pt-10">
        <p className={eyebrow}>gptme/stats</p>
        <h1 className="m-0 text-[44px] font-semibold leading-[1.1] tracking-display text-heading max-sm:text-[34px]">
          Project stats
        </h1>
        <p className={lede}>
          Usage and community numbers for gptme: GitHub stars, PyPI and release downloads, and repository activity.
          They are collected daily by the <a href={links.stats}>gptme/stats</a> repository, which is the source of
          truth for these figures. To see what caused a spike, check the <a href={links.timeline}>timeline</a>.
        </p>
        <p className="m-0 text-sm text-muted-text">
          Latest snapshot: <time dateTime={s.generated_at}>{formatDate(s.generated_at)}</time> (UTC)
          {stats.source === "fallback" && " (cached copy, gptme/stats was unreachable at build time)"}
          {" · "}
          <a href={dataFile("summary.json")}>summary.json</a>
        </p>
      </header>

      <Section id="stars" title="GitHub stars">
        <p className={lede}>
          Stargazers of <a href={links.github}>gptme/gptme</a>. Stars can be removed, so the totals are net of
          unstars.
        </p>
        <Tiles
          items={[
            { label: "Stars", value: s.stars },
            { label: "Added, last 7 days", value: s.stars_last_7d },
            { label: "Added, previous 7 days", value: s.stars_prev_7d },
            // Older summaries undercounted stars_last_30d (it only counted stars.csv rows);
            // gptme/stats#4 fixed that and added stars_last_7d at the same time.
            { label: "Added, last 30 days", value: s.stars_last_7d === undefined ? undefined : s.stars_last_30d },
          ]}
        />
        <Charts charts={charts.stars} />
      </Section>

      <Section id="pypi" title="PyPI downloads">
        <p className={lede}>
          Downloads of the <a href="https://pypi.org/project/gptme/">gptme</a> package from{" "}
          <a href="https://pypistats.org/packages/gptme">pypistats.org</a>, without mirrors. CI runs and caches make
          these approximate, so read them as a trend rather than a user count.
        </p>
        <Tiles
          items={[
            {
              label: "Last day",
              value: pypi?.last_day?.downloads,
              note: pypi?.last_day ? formatDate(pypi.last_day.date) : undefined,
            },
            { label: "Last 7 days", value: pypi?.last_7d },
            { label: "Last 30 days", value: pypi?.last_30d },
            {
              label: "Tracked total",
              value: pypi?.tracked_total,
              note: pypi?.tracked_since ? `since ${formatDate(pypi.tracked_since)}` : undefined,
            },
          ]}
        />
        <Charts charts={charts.pypi} />
      </Section>

      <Section id="releases" title="Release downloads">
        <p className={lede}>
          Downloads of binaries attached to GitHub releases, summed over every release. Installs with pip, pipx or uv
          are counted under PyPI instead.
        </p>
        <Tiles
          items={[
            { label: "gptme/gptme releases", value: s.release_downloads },
            { label: "gptme-tauri releases", value: s.tauri_release_downloads, note: "desktop app" },
          ]}
        />
        <Charts charts={charts.releases} />
      </Section>

      <Section id="repository" title="Repository">
        <p className={lede}>Current state of gptme/gptme on GitHub. Daily history is in daily.csv.</p>
        <Tiles
          items={[
            { label: "Contributors", value: s.contributors },
            { label: "Forks", value: s.forks },
            { label: "Watchers", value: s.watchers },
            { label: "Open issues", value: s.open_issues },
            { label: "Open pull requests", value: s.open_prs },
          ]}
        />
      </Section>

      <Section id="data" title="Data">
        <p className={lede}>
          Everything on this page comes from files in gptme/stats, updated by a daily GitHub Actions run. Definitions
          and caveats are in its <a href={`${links.stats}#definitions-and-caveats`}>README</a>.
        </p>
        <ul className="m-0 flex list-none flex-col gap-2 p-0 text-[15px] text-ink-2">
          {[
            ["summary.json", "latest numbers, for websites and scripts"],
            ["daily.csv", "one snapshot per day: stars, forks, watchers, issues, PRs, contributors, release downloads"],
            ["stars.csv", "when each current stargazer starred the repo"],
            ["pypi_daily.csv", "PyPI downloads per day"],
          ].map(([file, desc]) => (
            <li key={file}>
              <a href={dataFile(file)} className="font-mono text-sm">
                {file}
              </a>
              <span className="text-muted-text">: {desc}</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
