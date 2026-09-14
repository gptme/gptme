// Server entry: renders each route to static HTML at build time
// (scripts/prerender.mjs) and on request in dev (vite.config.ts).
import { renderToStaticMarkup } from "react-dom/server";
import { Head, Layout, type PageMeta } from "./components/Layout";
import { renderReadme } from "./lib/readme";
import { getStats } from "./lib/stats";
import { Home } from "./routes/Home";
import { Readme } from "./routes/Readme";

export const routes = [
  { path: "/", file: "index.html" },
  { path: "/readme/", file: "readme/index.html" },
];

export async function render(path: string): Promise<{ head: string; html: string }> {
  const stats = await getStats();

  let meta: PageMeta;
  let page: JSX.Element;
  if (path === "/readme/") {
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
