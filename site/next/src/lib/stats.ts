// Build-time stats from the gptme/stats repo.
//
// gptme/stats collects the numbers daily and publishes data/summary.json and
// charts/*.svg at stable raw.githubusercontent.com URLs. The site reads them at
// build time, and docs.yml rebuilds gptme.org on a daily schedule, so the
// numbers stay current without committing data into this repo.
//
// The build must never fail because GitHub is unreachable (offline builds,
// rate limits), so every field falls back to a committed default and a
// warning is logged instead.

export const STATS_RAW = "https://raw.githubusercontent.com/gptme/stats/master";
const STATS_URL = `${STATS_RAW}/data/summary.json`;

export interface StatsSummary {
  /** UTC date of the latest snapshot, YYYY-MM-DD */
  generated_at: string;
  stars: number | null;
  stars_last_7d?: number | null;
  stars_prev_7d?: number | null;
  stars_last_30d?: number | null;
  forks?: number | null;
  watchers?: number | null;
  open_issues?: number | null;
  open_prs?: number | null;
  contributors: number | null;
  release_downloads?: number | null;
  tauri_release_downloads?: number | null;
  pypi?: {
    package: string;
    last_day?: { date: string; downloads: number };
    last_7d?: number;
    last_30d?: number;
    tracked_total?: number;
    tracked_since?: string;
  };
}

// Committed fallback, a copy of gptme/stats data/summary.json from 2026-10-13.
// stars_last_30d is left out: that snapshot undercounted it (fixed in gptme/stats#4).
const DEFAULT_SUMMARY: StatsSummary = {
  generated_at: "2026-10-13",
  stars: 4448,
  forks: 451,
  watchers: 40,
  open_issues: 7,
  open_prs: 31,
  contributors: 57,
  release_downloads: 5365,
  tauri_release_downloads: 79,
  pypi: {
    package: "gptme",
    last_day: { date: "2026-10-12", downloads: 969 },
    last_7d: 6430,
    last_30d: 33972,
    tracked_total: 215675,
    tracked_since: "2026-03-17",
  },
};

export interface SiteStats {
  stars: number;
  starsLabel: string;
  contributors: number;
  summary: StatsSummary;
  source: "live" | "fallback";
}

/** 4413 -> "4.4k", 12345 -> "12k", 950 -> "950" */
export function formatCount(n: number): string {
  if (n < 1000) return String(n);
  const k = n / 1000;
  const rounded = k < 10 ? Math.round(k * 10) / 10 : Math.round(k);
  return `${rounded}k`.replace(".0k", "k");
}

/** 215675 -> "215,675"; missing values render as "n/a". */
export function formatInt(n: number | null | undefined): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("en-US") : "n/a";
}

/** "2026-10-13" -> "Oct 13, 2026" (UTC, so the build machine's zone doesn't matter) */
export function formatDate(iso: string | undefined): string {
  if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso ?? "n/a";
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

let cached: Promise<SiteStats> | undefined;

function fromSummary(summary: StatsSummary, source: SiteStats["source"]): SiteStats {
  const stars = summary.stars ?? (DEFAULT_SUMMARY.stars as number);
  const contributors = summary.contributors ?? (DEFAULT_SUMMARY.contributors as number);
  return { stars, contributors, starsLabel: formatCount(stars), summary, source };
}

async function load(): Promise<SiteStats> {
  try {
    const res = await fetch(STATS_URL, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as StatsSummary;
    if (typeof data.stars !== "number" || typeof data.contributors !== "number") {
      throw new Error("summary.json is missing stars/contributors");
    }
    if (typeof data.generated_at !== "string") throw new Error("summary.json is missing generated_at");
    return fromSummary(data, "live");
  } catch (err) {
    console.warn(
      `[site-next] could not fetch ${STATS_URL} (${(err as Error).message}); using committed defaults`,
    );
    return fromSummary(DEFAULT_SUMMARY, "fallback");
  }
}

/** Fetched once per build and shared by every page. */
export function getStats(): Promise<SiteStats> {
  cached ??= load();
  return cached;
}

export interface Chart {
  /** file name under charts/ in gptme/stats */
  file: string;
  alt: string;
  /** data file the chart is drawn from, linked under the chart */
  data: string;
  /**
   * Charts that gptme/stats has published for a while. If the existence check
   * can't reach GitHub they are still shown; newer charts are then left out.
   */
  established: boolean;
}

export interface ResolvedChart extends Chart {
  src: string;
  dataHref: string;
}

/**
 * Keep only the charts gptme/stats actually publishes, so a chart that is
 * planned (or retired) there shows up (or disappears) on the next build
 * without a site change. A definite 404 drops the chart; a network error
 * keeps established charts only.
 */
export async function resolveCharts(charts: Chart[]): Promise<ResolvedChart[]> {
  const checked = await Promise.all(
    charts.map(async (chart) => {
      const src = `${STATS_RAW}/charts/${chart.file}`;
      let present = chart.established;
      try {
        const res = await fetch(src, { method: "HEAD", signal: AbortSignal.timeout(8000) });
        present = res.ok;
        if (!res.ok && res.status !== 404) {
          console.warn(`[site-next] HEAD ${src} returned HTTP ${res.status}`);
          present = chart.established;
        }
      } catch (err) {
        console.warn(`[site-next] could not check ${src} (${(err as Error).message})`);
      }
      const dataHref = `https://github.com/gptme/stats/blob/master/data/${chart.data}`;
      return present ? { ...chart, src, dataHref } : null;
    }),
  );
  return checked.filter((c): c is ResolvedChart => c !== null);
}
