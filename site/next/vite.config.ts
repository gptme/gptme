import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";

type Route = { path: string; file: string };
type EntryServer = {
  routes: Route[];
  render: (path: string) => Promise<{ head: string; html: string }>;
};

/**
 * Dev server: render pages with the same SSR entry the production prerender
 * uses, so `npm run dev` shows real server-rendered HTML (no client SPA).
 */
function ssrDev(): Plugin {
  return {
    name: "gptme-site-ssr-dev",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const url = req.originalUrl ?? "/";
        const path = url.split(/[?#]/)[0];
        try {
          const entry = (await server.ssrLoadModule("/src/entry-server.tsx")) as EntryServer;
          const route = entry.routes.find((r) => r.path === path || r.path === `${path}/`);
          if (!route) return next();
          const template = await server.transformIndexHtml(
            url,
            readFileSync(resolve(process.cwd(), "index.html"), "utf8"),
          );
          const { head, html } = await entry.render(route.path);
          res.setHeader("Content-Type", "text/html; charset=utf-8");
          res.end(template.replace("<!--app-head-->", () => head).replace("<!--app-html-->", () => html));
        } catch (err) {
          server.ssrFixStacktrace(err as Error);
          next(err);
        }
      });
    },
  };
}

export default defineConfig({
  appType: "custom",
  plugins: [ssrDev()],
  esbuild: {
    jsx: "automatic",
  },
});
