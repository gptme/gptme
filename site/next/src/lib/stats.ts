// Build-time stats from the gptme/stats repo.
//
// The build must never fail because GitHub is unreachable (offline builds,
// rate limits), so every field falls back to a committed default and a
// warning is logged instead.

const STATS_URL =
  "https://raw.githubusercontent.com/gptme/stats/master/data/summary.json";

// Committed fallbacks, last refreshed from gptme/stats on 2026-09-14.
const DEFAULTS = {
  stars: 4413,
  contributors: 50,
};

export interface SiteStats {
  stars: number;
  starsLabel: string;
  contributors: number;
  source: "live" | "fallback";
}

/** 4413 -> "4.4k", 12345 -> "12k", 950 -> "950" */
export function formatCount(n: number): string {
  if (n < 1000) return String(n);
  const k = n / 1000;
  const rounded = k < 10 ? Math.round(k * 10) / 10 : Math.round(k);
  return `${rounded}k`.replace(".0k", "k");
}

let cached: Promise<SiteStats> | undefined;

async function load(): Promise<SiteStats> {
  try {
    const res = await fetch(STATS_URL, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as Record<string, unknown>;
    const stars = typeof data.stars === "number" ? data.stars : undefined;
    const contributors =
      typeof data.contributors === "number" ? data.contributors : undefined;
    if (stars === undefined || contributors === undefined) {
      throw new Error("summary.json is missing stars/contributors");
    }
    return { stars, contributors, starsLabel: formatCount(stars), source: "live" };
  } catch (err) {
    console.warn(
      `[site-next] could not fetch ${STATS_URL} (${(err as Error).message}); using committed defaults`,
    );
    return {
      ...DEFAULTS,
      starsLabel: formatCount(DEFAULTS.stars),
      source: "fallback",
    };
  }
}

/** Fetched once per build and shared by every page. */
export function getStats(): Promise<SiteStats> {
  cached ??= load();
  return cached;
}
