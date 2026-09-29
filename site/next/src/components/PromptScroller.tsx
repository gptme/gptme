/**
 * Scrolling CLI prompt sample, ported from the gptme.ai landing hero
 * (`ScrollingPrompts.tsx` in gptme-cloud, retired in gptme/gptme-cloud#1059).
 *
 * Erik: "the current hero, which is just a bunch of scrolling prompts with
 * CLI-style animation ... should maybe move to the gptme.org site now because
 * it's more CLI than gptme-cloud-inspired."
 *
 * Adapted to gptme.org, which is prerendered with no React hydration
 * (`src/client.ts`), so the scroll is a pure-CSS animation over a duplicated
 * list — no hooks, no JS, no shadcn/lucide dependencies. It pauses on hover,
 * focus and press, and stops entirely under `prefers-reduced-motion: reduce`.
 * Colours use the site's terminal tokens; the category tag reuses the terminal
 * tag styling.
 */

type PromptKind =
  | "coding"
  | "ai"
  | "github"
  | "terminal"
  | "browser"
  | "bot"
  | "vision"
  | "social"
  | "discord";

const KIND_LABEL: Record<PromptKind, string> = {
  coding: "coding",
  ai: "AI",
  github: "github",
  terminal: "terminal",
  browser: "browser",
  bot: "bot",
  vision: "vision",
  social: "social",
  discord: "discord",
};

const prompts: Array<{ prompt: string; kind: PromptKind }> = [
  { prompt: '$ gptme "build a 3D game using three.js with particles and physics"', kind: "coding" },
  { prompt: '$ gptme "create a neural network to recognize handwritten digits"', kind: "ai" },
  { prompt: '$ git diff | gptme "review this PR and suggest improvements"', kind: "github" },
  { prompt: '$ gptme "render mandelbrot set to mandelbrot.png"', kind: "terminal" },
  { prompt: '$ gptme "create a web scraper for product prices on e-commerce sites"', kind: "browser" },
  { prompt: '$ gptme "write a trading bot that monitors cryptocurrency prices"', kind: "bot" },
  { prompt: '$ gptme "analyze this dataset and create visualizations" data.csv', kind: "coding" },
  { prompt: '$ gptme "take a screenshot and suggest UI improvements"', kind: "vision" },
  { prompt: '$ gptme "implement a secure authentication system with JWT"', kind: "coding" },
  { prompt: '$ gptme "create a realtime chat application with websockets"', kind: "discord" },
  { prompt: '$ gptme "schedule a daily task to backup my database and email me the status"', kind: "terminal" },
  { prompt: '$ make test | gptme "fix the failing tests"', kind: "coding" },
  { prompt: '$ gptme "optimize my webpack configuration for faster builds"', kind: "coding" },
  { prompt: '$ gptme "create a RESTful API with Express and MongoDB"', kind: "coding" },
  { prompt: '$ gptme "what do you see?" image.png', kind: "vision" },
  { prompt: '$ gptme "implement this" https://github.com/gptme/gptme/issues/286', kind: "github" },
  { prompt: '$ git status -vv | gptme "fix TODOs in components and commit"', kind: "github" },
  { prompt: '$ gptme "suggest improvements to my vimrc"', kind: "terminal" },
  { prompt: '$ gptme "create a Twitter bot that posts daily AI news"', kind: "social" },
  { prompt: '$ gptme "build a recommendation system based on user preferences"', kind: "ai" },
];

const QUOTED = /"([^"]*?)"/g;
const FILENAME =
  /(?<!")((https?:\/\/[^\s"]+)|(\S+\.(png|jpg|jpeg|gif|svg|zip|pdf|html|css|js|csv|xlsx|json|xml|md)))(?!")/g;

type Span = { start: number; end: number; text: string };

/**
 * Non-overlapping spans to highlight, earliest first. A quoted string and a
 * filename nested inside it both match, so the quoted span (longer, and
 * starting at or before the nested one) must win — otherwise the filename would
 * be emitted twice.
 */
function highlightSpans(text: string): Span[] {
  const spans: Span[] = [];
  for (const re of [QUOTED, FILENAME]) {
    for (const match of text.matchAll(re)) {
      if (match.index !== undefined) spans.push({ start: match.index, end: match.index + match[0].length, text: match[0] });
    }
  }
  return spans.sort((a, b) => a.start - b.start || b.end - a.end);
}

/** Terminal-style syntax highlighting for one `$ …` prompt line. */
function formatPrompt(prompt: string) {
  const parts = prompt.split("$");
  if (parts.length <= 1) return <span>{prompt}</span>;

  const out: Array<JSX.Element> = [
    <span key="dollar" className="text-term-prompt">
      ${" "}
    </span>,
  ];

  parts[1]
    .trim()
    .split("|")
    .forEach((part, pipeIndex) => {
      const trimmed = part.trim();

      if (pipeIndex > 0) {
        out.push(
          <span key={`pipe-${pipeIndex}`} className="text-term-muted">
            {" | "}
          </span>,
        );
      }

      const command = trimmed.match(/^(\S+)/)?.[0];
      if (!command) return;

      out.push(
        <span key={`cmd-${pipeIndex}`} className="text-term-pass">
          {command}
        </span>,
      );

      // Everything after the command, whitespace preserved.
      const rest = trimmed.slice(command.length);
      let pos = 0;
      let span = 0;
      for (const { start, end, text } of highlightSpans(rest)) {
        if (start < pos) continue; // already covered by an earlier span
        if (start > pos) {
          out.push(<span key={`t-${pipeIndex}-${span++}`}>{rest.slice(pos, start)}</span>);
        }
        out.push(
          <span key={`hl-${pipeIndex}-${span++}`} className="text-term-tool">
            {text}
          </span>,
        );
        pos = end;
      }
      if (pos < rest.length) {
        out.push(<span key={`t-${pipeIndex}-${span++}`}>{rest.slice(pos)}</span>);
      }
    });

  return <>{out}</>;
}

function Row({ prompt, kind }: { prompt: string; kind: PromptKind }) {
  return (
    <li className="flex items-center justify-between gap-3 px-3 py-1 max-sm:gap-2 max-sm:px-2.5">
      <span className="min-w-0 truncate text-[13px] max-sm:text-[11px]">{formatPrompt(prompt)}</span>
      <span className="flex-shrink-0 rounded-sm bg-term-header px-1.5 py-0.5 text-[11px] text-term-tool max-sm:text-[10px]">
        {KIND_LABEL[kind]}
      </span>
    </li>
  );
}

export function PromptScroller() {
  return (
    <div
      className="prompt-scroller relative h-[190px] overflow-hidden rounded-lg border border-term-border bg-term-bg font-mono text-term-text shadow-term max-sm:h-[150px] max-sm:rounded-[10px]"
      role="group"
      aria-label="Examples of what you can ask gptme to do. Focus, hover or press to pause."
      tabIndex={0}
    >
      <div
        className="pointer-events-none absolute inset-x-0 top-0 z-10 h-12 bg-gradient-to-b from-term-bg to-transparent"
        aria-hidden="true"
      />
      <div className="prompt-scroller-track">
        <ul className="m-0 flex list-none flex-col p-0">
          {prompts.map((p) => (
            <Row key={p.prompt} {...p} />
          ))}
        </ul>
        {/* Duplicate for a seamless loop; hidden from assistive tech. */}
        <ul className="m-0 flex list-none flex-col p-0" aria-hidden="true">
          {prompts.map((p) => (
            <Row key={`dup-${p.prompt}`} {...p} />
          ))}
        </ul>
      </div>
      <div
        className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-12 bg-gradient-to-t from-term-bg to-transparent"
        aria-hidden="true"
      />
    </div>
  );
}

export default PromptScroller;
