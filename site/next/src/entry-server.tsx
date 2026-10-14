// Server entry: renders each route to static HTML at build time
// (scripts/prerender.mjs) and on request in dev (vite.config.ts).
import { renderToStaticMarkup } from "react-dom/server";
import { Head, Layout, type PageMeta } from "./components/Layout";
import { renderReadme } from "./lib/readme";
import { getStats, resolveCharts } from "./lib/stats";
import { Home } from "./routes/Home";
import { Readme } from "./routes/Readme";
import { Stats, statsCharts } from "./routes/Stats";

export const routes = [
  { path: "/", file: "index.html" },
  { path: "/readme/", file: "readme/index.html" },
  { path: "/stats/", file: "stats/index.html" },
];

export async function render(path: string): Promise<{ head: string; html: string }> {
  const stats = await getStats();

  let meta: PageMeta;
  let page: JSX.Element;
  if (path === "/stats/") {
    meta = {
      title: "Stats · gptme",
      description:
        "gptme usage and community stats: GitHub stars, PyPI and release downloads, contributors. Collected daily by gptme/stats.",
      path,
    };
    const [stars, pypi, releases] = await Promise.all([
      resolveCharts(statsCharts.stars),
      resolveCharts(statsCharts.pypi),
      resolveCharts(statsCharts.releases),
    ]);
    page = <Stats stats={stats} charts={{ stars, pypi, releases }} />;
  } else if (path === "/readme/") {
    meta = {
      title: "README · gptme",
      description: "The gptme README: features, demos, getting started and the ecosystem.",
      path,
    };
    page = <Readme html={renderReadme()} />;
  } else {
    meta = {
      title: "gptme: an AI agent that lives in your terminal",
      description:
        "gptme is an open-source AI agent that lives in your terminal. Bring Claude, GPT, Gemini, Grok, DeepSeek, or a local model.",
      path: "/",
    };
    page = <Home stats={stats} />;
  }

  return {
    head: renderToStaticMarkup(<Head {...meta} />),
    html: renderToStaticMarkup(<Layout stats={stats}>{page}</Layout>),
  };
}
