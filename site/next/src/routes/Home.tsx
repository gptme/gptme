import { CheckIcon, CopyIcon } from "../components/icons";
import { cn, eyebrow, wrap } from "../lib/cn";
import { links } from "../lib/links";
import type { SiteStats } from "../lib/stats";

const surfaces = [
  { tag: "$ gptme", title: "CLI", desc: "Interactive, or non-interactive for scripts and CI.", href: links.cli },
  { tag: "$ gptme-tui", title: "Terminal UI", desc: "A full-screen interface for long sessions.", href: links.tui },
  {
    tag: "chat.gptme.org",
    title: "Web UI",
    desc: "Bundled with gptme-server. Point it at your own machine.",
    href: links.webui,
  },
  { tag: "linux · macos · windows", title: "Desktop app", desc: "Native app with auto-updates.", href: links.downloads },
  { tag: "zed · jetbrains", title: "Your editor", desc: "A drop-in coding agent over ACP.", href: links.acp },
  { tag: "rest · mcp · acp", title: "Server", desc: "A REST API, plus MCP and ACP in both directions.", href: links.server },
];

const ecosystem = [
  {
    tag: "github · email · chat · voice",
    title: "Reachable where you already are",
    href: links.channels,
    desc: "Mention @gptme on an issue or pull request. Let an agent read and answer email, sit in Discord or WhatsApp, or take a phone call. Agents can also message each other over SSH.",
  },
  {
    tag: "mcp · acp",
    title: "MCP in both directions",
    href: links.mcp,
    desc: "Load any MCP server as gptme tools — or run gptme as an MCP server, handing its persistent shell, Python session and file tools to Claude Desktop, Cursor or anything else that speaks MCP.",
  },
  {
    tag: "plugins · skills · lessons",
    title: "Extend it without forking it",
    href: links.plugins,
    desc: "Plugins add tools, hooks and commands. Skills and lessons teach it your workflows in plain Markdown. gptme-contrib carries community packages: code graphs, multi-model consensus, LSP, image generation.",
  },
];

// What each tool actually does, linked to its reference page. The full set is
// larger (28 documented tools); these are the ones worth showing on a landing page.
const toolDocs: Array<{ name: string; href: string; desc: string }> = [
  {
    name: "shell",
    href: links.tool.shell,
    desc: "A persistent session: builds, tests, git, ssh, package managers — with state kept between commands.",
  },
  {
    name: "ipython",
    href: links.tool.python,
    desc: "A stateful Python REPL with your installed libraries, so data stays loaded between steps.",
  },
  {
    name: "patch · morph",
    href: links.tool.patch,
    desc: "Surgical edits to existing files instead of rewriting them wholesale.",
  },
  {
    name: "browser",
    href: links.tool.browser,
    desc: "A real Chromium: search, read pages, click, fill forms, scroll, screenshot, read PDFs.",
  },
  {
    name: "computer",
    href: links.tool.computer,
    desc: "The desktop itself on X11 or macOS — mouse, keyboard and screen capture, for apps with no API.",
  },
  {
    name: "subagent",
    href: links.tool.subagent,
    desc: "Spawns child agents in parallel or as a pipeline, each with its own context, and steers or cancels them mid-run.",
  },
  {
    name: "tmux",
    href: links.tool.tmux,
    desc: "Long-running processes it can come back to: dev servers, log tails, REPLs.",
  },
  {
    name: "vision · screenshot",
    href: links.tool.vision,
    desc: "Reads images, diagrams, its own screenshots and rendered pages.",
  },
];

/** Shown only above 600px. The mobile board uses shorter terminal lines. */
const wide = "max-sm:hidden";
/** Shown only at 600px and below. */
const narrow = "hidden max-sm:inline";

const toolBox = "overflow-hidden rounded-sm border border-term-border";
const toolLabel =
  "bg-term-header px-3 py-1.5 text-[12.5px] text-term-tool max-sm:px-[10px] max-sm:py-1 max-sm:text-[10.5px]";
const toolOut = "m-0 overflow-x-auto whitespace-pre px-3 py-[10px] [font:inherit] max-sm:px-[10px] max-sm:py-2";

function Hero() {
  return (
    <div
      className={cn(
        wrap,
        "grid grid-cols-[7fr_5fr] items-center gap-12 pb-[92px] pt-[84px]",
        "max-md:grid-cols-1 max-md:gap-8 max-md:pb-16 max-md:pt-12 max-sm:gap-[22px] max-sm:pb-12 max-sm:pt-7",
      )}
    >
      <div className="flex min-w-0 flex-col gap-7 max-sm:gap-[22px]">
        <p className={eyebrow}>Open source · MIT · Any model</p>
        <h1 className="m-0 font-mono text-[58px] font-semibold leading-[1.08] tracking-display text-heading [text-wrap:balance] max-lg:text-[48px] max-md:text-[44px] max-sm:text-[34px] max-sm:leading-[1.12] max-sm:tracking-heading">
          An AI agent that lives in your terminal.
        </h1>
        <p className="m-0 max-w-[600px] text-xl leading-[1.6] text-ink-2 [text-wrap:pretty] max-sm:text-[17px] max-sm:leading-[1.6]">
          One agent you run yourself, extend with your own tools, and can leave running on its own. Anywhere a terminal
          runs — laptop, ssh, tmux, headless servers, CI — on any model, including local ones.
        </p>
        <div className="flex flex-col gap-[10px] max-sm:gap-3">
          <div className="flex flex-wrap items-center gap-3 max-sm:gap-x-[10px]">
            <div className="flex min-h-[53px] items-center justify-between gap-[22px] rounded-md bg-term-bg py-[10px] pl-[18px] pr-[10px] font-mono text-[17px] text-term-text max-sm:min-h-12 max-sm:flex-[1_1_100%] max-sm:gap-3 max-sm:py-2 max-sm:pl-4 max-sm:pr-2 max-sm:text-[15px]">
              <code className="flex gap-3 whitespace-nowrap [font:inherit] max-sm:gap-[10px]">
                <span className="select-none text-term-prompt" aria-hidden="true">
                  $
                </span>
                <span>pipx install gptme</span>
              </code>
              <button
                type="button"
                data-copy="pipx install gptme"
                aria-label="Copy install command"
                hidden
                className="group inline-flex h-8 w-8 items-center justify-center rounded-sm text-term-muted hover:bg-term-header hover:text-term-text"
              >
                <CopyIcon className="group-data-[state=copied]:hidden" />
                <CheckIcon className="hidden text-term-pass group-data-[state=copied]:block" />
              </button>
            </div>
            <a
              href={links.gettingStarted}
              className="inline-flex items-center justify-center whitespace-nowrap rounded-md bg-accent px-[22px] py-[15px] text-base font-semibold text-on-accent hover:text-on-accent hover:brightness-110 max-sm:min-h-12 max-sm:flex-1 max-sm:px-3 max-sm:py-0"
            >
              Get started
            </a>
            <a
              href={links.docs}
              className="inline-flex items-center justify-center whitespace-nowrap rounded-md border border-border bg-surface px-5 py-[14px] text-base font-medium text-ink hover:border-muted hover:text-ink max-sm:min-h-12 max-sm:flex-1 max-sm:px-3 max-sm:py-0"
            >
              Read the docs
            </a>
          </div>
          <span className="font-mono text-[13px] text-muted-text max-sm:text-xs">or: uv tool install gptme</span>
          <span className="sr-only" id="copy-status" role="status" aria-live="polite" />
        </div>
      </div>

      <div className="relative flex h-[470px] items-center justify-center max-lg:h-[400px] max-md:order-first max-md:h-[270px]">
        <svg
          viewBox="0 0 470 470"
          fill="none"
          aria-hidden="true"
          className="absolute h-[470px] w-[470px] max-lg:h-[400px] max-lg:w-[400px] max-md:h-[270px] max-md:w-[270px]"
        >
          {/* At 270px the SVG is scaled down, so strokes and dashes are compensated to look the same. */}
          <circle
            className="stroke-ring max-md:[stroke-width:2.6]"
            cx="235"
            cy="235"
            r="228"
            strokeOpacity="0.45"
            strokeWidth="1.5"
          />
          <circle
            className="stroke-accent max-md:[stroke-dasharray:5.2_13.9] max-md:[stroke-width:2.6]"
            cx="235"
            cy="235"
            r="184"
            strokeOpacity="0.35"
            strokeWidth="1.5"
            strokeDasharray="3 9"
          />
          <circle
            className="stroke-ring max-md:[stroke-width:2.6]"
            cx="235"
            cy="235"
            r="140"
            strokeOpacity="0.75"
            strokeWidth="1.5"
          />
        </svg>
        <img
          src="/media/logo.png"
          alt="The gptme mascot: a friendly robot head surrounded by tools"
          width={250}
          height={250}
          className="relative h-auto w-[250px] max-lg:w-[212px] max-md:w-[148px]"
        />
      </div>
    </div>
  );
}

function Terminal() {
  return (
    <figure className="m-0 flex min-w-0 flex-col gap-[10px]">
      <div className="overflow-hidden rounded-lg border border-term-border bg-term-bg font-mono text-term-text shadow-term max-sm:rounded-[10px]">
        <div className="flex justify-between gap-4 border-b border-term-border px-[18px] py-3 text-[13px] text-term-muted max-sm:px-[14px] max-sm:py-[10px] max-sm:text-[11px]">
          <span>~/projects/api</span>
          <span>gptme · claude</span>
        </div>
        <div className="flex flex-col gap-[14px] p-6 text-[14.5px] leading-[1.6] max-sm:gap-[10px] max-sm:p-[14px] max-sm:text-[11.5px] max-sm:leading-[1.55]">
          <div>
            <span className="text-term-prompt">$</span> gptme "fix the 500 on empty names
            <span className={wide}> in /users</span>"
          </div>
          <div className="text-term-muted">Let me reproduce it first.</div>
          <div className={toolBox}>
            <div className={toolLabel}>shell</div>
            <pre className={cn(toolOut, "max-sm:pb-[2px]")}>
              {"pytest tests/test_users.py -x"}
              <span className={wide}>{" -q"}</span>
            </pre>
            <pre className={cn(toolOut, "pt-0 text-term-fail max-sm:pt-0")}>{"F  1 failed, 11 passed"}</pre>
          </div>
          <div className={cn("text-term-muted", wide)}>
            The name is indexed before it's checked for emptiness. Patching.
          </div>
          <div className={toolBox}>
            <div className={toolLabel}>patch api/validators.py</div>
            <pre className={toolOut}>
              <span className="text-term-fail">
                {"-    initial = name.strip()[0]"}
                <span className={wide}>{".upper()"}</span>
              </span>
              {"\n"}
              <span className="text-term-pass">{"+    name = name.strip()"}</span>
              {"\n"}
              <span className="text-term-pass">{"+    if not name:"}</span>
              {"\n"}
              <span className="text-term-pass">
                {"+        raise "}
                <span className={wide}>{'ValidationError("name is required")'}</span>
                <span className={narrow}>{'ValueError("empty")'}</span>
              </span>
              {"\n"}
              <span className="text-term-pass">
                {"+    initial = name[0]"}
                <span className={wide}>{".upper()"}</span>
              </span>
            </pre>
          </div>
          <div className={toolBox}>
            <div className={toolLabel}>shell</div>
            <pre className={cn(toolOut, "max-sm:pb-[2px]")}>
              {"pytest tests/test_users.py"}
              <span className={wide}>{" -q"}</span>
            </pre>
            <pre className={cn(toolOut, "pt-0 text-term-pass max-sm:pt-0")}>{"12 passed"}</pre>
          </div>
          <div className="text-term-muted">
            Fixed: empty names now return 422<span className={wide}> instead of 500</span>.
          </div>
        </div>
      </div>
      <figcaption className="text-right text-[13px] text-muted-text max-sm:text-left max-sm:text-xs">
        Illustrative session. The live site replays a recorded run.
      </figcaption>
    </figure>
  );
}

function ToolGrid() {
  return (
    <div className="mt-16 border-t border-border pt-12 max-md:mt-12 max-md:pt-10 max-sm:mt-9 max-sm:pt-8">
      <div className="mb-7 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 max-sm:mb-5 max-sm:flex-col max-sm:items-start max-sm:gap-3">
        <h3 className="m-0 text-[26px] font-semibold leading-[1.25] tracking-heading text-heading max-sm:text-[21px]">
          What it can actually do
        </h3>
        <a href={links.tools} className="text-base font-medium text-accent hover:text-accent-2-text max-sm:text-[15px]">
          Full tool reference →
        </a>
      </div>
      <ul className="m-0 grid list-none grid-cols-4 gap-x-8 gap-y-7 p-0 max-lg:grid-cols-3 max-md:grid-cols-2 max-sm:grid-cols-1 max-sm:gap-y-5">
        {toolDocs.map((t) => (
          <li key={t.name} className="flex flex-col gap-1.5 max-sm:gap-1">
            <a href={t.href} className="font-mono text-[15px] font-medium text-accent hover:text-accent-2-text">
              {t.name}
            </a>
            <p className="m-0 text-[15px] leading-[1.55] text-ink-2 max-sm:text-[14.5px]">{t.desc}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SeeItWork() {
  return (
    <section className="border-y border-border bg-surface" aria-labelledby="demo-title">
      <div className={cn(wrap, "py-22 max-md:py-16 max-sm:py-12")}>
      <div
        className={cn(
          "grid grid-cols-[5fr_7fr] items-center gap-16",
          "max-md:grid-cols-1 max-md:gap-10 max-sm:gap-[18px]",
        )}
      >
        <div className="flex min-w-0 flex-col gap-5 max-sm:gap-[18px]">
          <p className={eyebrow}>See it work</p>
          <h2
            id="demo-title"
            className="m-0 text-[38px] font-semibold leading-[1.18] tracking-heading text-heading [text-wrap:balance] max-sm:text-[27px] max-sm:leading-[1.2]"
          >
            Your machine, your tools, no sandbox in the way.
          </h2>
          <p className="m-0 text-lg leading-[1.6] text-ink-2 [text-wrap:pretty] max-sm:text-base max-sm:leading-[1.6]">
            It works in your real environment: your shell and Python, your files, your git and gh setup, a browser and
            the whole desktop when a task needs one.
            <span className={wide}>
              {" "}
              Point it at a local model and nothing leaves the machine at all.
            </span>
          </p>
        </div>
        <Terminal />
      </div>
      <ToolGrid />
      </div>
    </section>
  );
}

const panel =
  "flex min-w-0 flex-col gap-[14px] rounded-lg border border-border bg-surface p-8 max-sm:gap-[10px] max-sm:p-[22px]";
const panelText = "m-0 text-[17px] leading-[1.6] text-ink-2 max-sm:text-[15px] max-sm:leading-[1.6]";

function AgentShell() {
  return (
    <div className="flex min-w-0 flex-col gap-4 max-sm:gap-3">
      <div className="overflow-hidden rounded-lg border border-term-border bg-term-bg font-mono text-term-text shadow-term max-sm:rounded-[10px]">
        <div className="flex justify-between gap-4 border-b border-term-border px-[18px] py-3 text-[13px] text-term-muted max-sm:px-[14px] max-sm:py-[10px] max-sm:text-[11px]">
          <span>~</span>
          <span>gptme-agent</span>
        </div>
        <div className="flex flex-col gap-[14px] p-6 text-[14.5px] leading-[1.6] max-sm:gap-[10px] max-sm:p-[14px] max-sm:text-[11.5px] max-sm:leading-[1.55]">
          <div>
            <span className="text-term-prompt">$</span> gptme-agent create ~/my-agent
            <span className={wide}> --name MyAgent</span>
          </div>
          <div className="text-term-muted">
            Workspace ready: journal, tasks, knowledge, lessons
            <span className={wide}> — all git-tracked</span>
          </div>
          <div>
            <span className="text-term-prompt">$</span> gptme-agent install
          </div>
          <div className="text-term-muted">
            Installed timer<span className={wide}>: MyAgent now wakes up on a schedule</span>
          </div>
          <div>
            <span className="text-term-prompt">$</span> gptme-agent status
          </div>
          <div className="text-term-pass">
            running · 3 tasks in queue<span className={wide}> · last run 14m ago</span>
          </div>
        </div>
      </div>
      <p className="m-0 text-[13px] text-muted-text max-sm:text-xs">
        On a headless box, <code className="font-mono">gptme service init</code> scaffolds the same thing as a systemd
        service. See <a href={links.autonomous}>running agents autonomously</a>.
      </p>
    </div>
  );
}

function Agents() {
  const traits: Array<[string, string]> = [
    ["A workspace it owns", "Git-tracked journal, tasks, knowledge and lessons. Its memory is a repo you can read."],
    ["Run loops", "systemd or launchd on a schedule, or triggered by events like a new issue or a failing build."],
    ["It learns in writing", "Lessons it writes for itself get matched back into later sessions, so mistakes stick."],
    ["More than one", "File leases, a message bus and work claims let several agents share a workspace without collisions."],
  ];
  return (
    <section className="border-y border-border bg-surface" aria-labelledby="agents-title">
      <div className={cn(wrap, "py-22 max-md:py-16 max-sm:py-12")}>
        <div
          className={cn(
            "grid grid-cols-[7fr_5fr] items-start gap-16",
            "max-md:grid-cols-1 max-md:gap-10 max-sm:gap-[22px]",
          )}
        >
          <div className="flex min-w-0 flex-col gap-5 max-sm:gap-[18px]">
            <p className={eyebrow}>Beyond the prompt</p>
            <h2
              id="agents-title"
              className="m-0 text-[38px] font-semibold leading-[1.18] tracking-heading text-heading [text-wrap:balance] max-sm:text-[27px] max-sm:leading-[1.2]"
            >
              Most of what gptme does, nobody is watching.
            </h2>
            <p className="m-0 text-lg leading-[1.6] text-ink-2 [text-wrap:pretty] max-sm:text-base max-sm:leading-[1.6]">
              gptme is built to keep working when you close the laptop. One command turns it into a persistent agent
              with a memory, a task queue and a schedule — the same scaffolding the agents below run on.
            </p>
            <dl className="m-0 grid grid-cols-2 gap-x-8 gap-y-6 max-sm:grid-cols-1 max-sm:gap-y-[18px]">
              {traits.map(([term, desc]) => (
                <div key={term} className="flex flex-col gap-1.5 max-sm:gap-1">
                  <dt className="text-[17px] font-semibold leading-[1.35] text-heading max-sm:text-base">{term}</dt>
                  <dd className="m-0 text-[15px] leading-[1.55] text-ink-2 max-sm:text-[14.5px]">{desc}</dd>
                </div>
              ))}
            </dl>
            <a
              href={links.agentTemplate}
              className="text-base font-medium text-accent hover:text-accent-2-text max-sm:text-[15px]"
            >
              gptme-agent-template →
            </a>
          </div>
          <AgentShell />
        </div>

        <div className="mt-14 grid grid-cols-[7fr_5fr] gap-16 border-t border-border pt-10 max-md:mt-10 max-md:grid-cols-1 max-md:gap-8 max-sm:mt-8 max-sm:gap-5 max-sm:pt-7">
          <div className="flex min-w-0 flex-col gap-[14px] max-sm:gap-[10px]">
            <p className={cn(eyebrow, "text-muted-text")}>The reference agent</p>
            <h3 className="m-0 text-[26px] font-semibold leading-[1.3] text-heading [text-wrap:balance] max-sm:text-[21px] max-sm:leading-[1.3]">
              Bob has been running on gptme since 2024, without taking a day off.
            </h3>
            <p className={panelText}>
              He picks up his own work, opens and reviews pull requests, fixes CI, writes blog posts, and keeps the
              lessons that make his next run better. A sibling agent, <a href={links.alice}>Alice</a>, runs the same
              template as a personal assistant. Fork either shape in a single command.
            </p>
            <a
              href={links.bobTimeline}
              className="text-base font-medium text-accent hover:text-accent-2-text max-sm:text-[15px]"
            >
              See Bob's timeline →
            </a>
          </div>
          <dl className="m-0 grid grid-cols-2 gap-x-6 gap-y-5 self-start max-sm:gap-x-3">
            {[
              ["4,000+", "merged public PRs"],
              ["24/7", "since late 2024"],
            ].map(([value, label]) => (
              <div key={label} className="flex flex-col-reverse justify-end gap-1 max-sm:gap-0.5">
                <dt className="text-[15px] leading-[1.4] text-muted-text max-sm:text-sm">{label}</dt>
                <dd className="m-0 font-mono text-[38px] font-semibold leading-[1.25] text-heading max-sm:text-[30px]">
                  {value}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </section>
  );
}

function Models() {
  return (
    <section className="border-y border-border bg-surface" aria-labelledby="models-title">
      <div
        className={cn(
          wrap,
          "grid grid-cols-[6fr_6fr] items-center gap-16 py-22",
          "max-md:grid-cols-1 max-md:gap-10 max-md:py-16 max-sm:gap-[22px] max-sm:py-12",
        )}
      >
        <div className="overflow-hidden rounded-lg border border-term-border bg-term-bg font-mono text-term-text shadow-term max-md:order-last max-sm:rounded-[10px]">
          <div className="flex justify-between gap-4 border-b border-term-border px-[18px] py-3 text-[13px] text-term-muted max-sm:px-[14px] max-sm:py-[10px] max-sm:text-[11px]">
            <span>~</span>
            <span>same tools, any model</span>
          </div>
          <div className="flex flex-col gap-[18px] overflow-x-auto p-6 text-[14px] leading-[1.6] max-sm:gap-[14px] max-sm:p-[14px] max-sm:text-[11px] max-sm:leading-[1.55]">
            {[
              {
                note: "# the subscription you already pay for — nothing per token",
                cmds: ["gptme -m openai-subscription/gpt-6-astra", "gptme -m grok-subscription/grok-4.6"],
              },
              {
                note: "# open-weight flash models: high volume at a fraction of the cost",
                cmds: [
                  "gptme -m openrouter/deepseek/deepseek-v4.1-flash",
                  "gptme -m openrouter/z-ai/glm-5.3-flash",
                ],
              },
              { note: "# or entirely local, nothing leaving the machine", cmds: ["gptme -m local/llama3.2:1b"] },
            ].map((group) => (
              <div key={group.note} className="flex flex-col gap-1.5 max-sm:gap-1">
                <div className="whitespace-pre text-term-muted">{group.note}</div>
                {group.cmds.map((c) => (
                  <div key={c} className="whitespace-pre">
                    <span className="select-none text-term-prompt" aria-hidden="true">
                      ${" "}
                    </span>
                    {c}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

        <div className="flex min-w-0 flex-col gap-5 max-sm:gap-[18px]">
          <p className={eyebrow}>Bring your own model</p>
          <h2
            id="models-title"
            className="m-0 text-[38px] font-semibold leading-[1.18] tracking-heading text-heading [text-wrap:balance] max-sm:text-[27px] max-sm:leading-[1.2]"
          >
            Never locked to one provider, or one price.
          </h2>
          <p className="m-0 text-lg leading-[1.6] text-ink-2 [text-wrap:pretty] max-sm:text-base max-sm:leading-[1.6]">
            One flag switches model, provider or price tier — the tools, config and workflow stay identical. Already
            paying for ChatGPT Plus/Pro or SuperGrok? Use that plan instead of an API key, at no cost per token.
            Running something high-volume? Open-weight models like DeepSeek V4.1 Flash and GLM 5.3 Flash hold up in
            agentic work for a fraction of frontier prices, and one OpenRouter key reaches 100+ models with
            data collection denied by default.
          </p>
          <div className="flex flex-wrap gap-x-7 gap-y-2">
            <a href={links.models} className="text-base font-medium text-accent hover:text-accent-2-text max-sm:text-[15px]">
              Picking a model →
            </a>
            <a
              href={links.providers}
              className="text-base font-medium text-accent hover:text-accent-2-text max-sm:text-[15px]"
            >
              Providers and subscriptions →
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}

function Ecosystem() {
  return (
    <section
      className={cn(wrap, "pb-18 pt-22 max-md:pb-12 max-md:pt-16 max-sm:pb-10 max-sm:pt-12")}
      aria-labelledby="ecosystem-title"
    >
      <div className="mb-8 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 max-sm:mb-4 max-sm:flex-col max-sm:items-start max-sm:gap-4">
        <h2
          id="ecosystem-title"
          className="m-0 text-[34px] font-semibold leading-[1.2] tracking-heading text-heading max-sm:text-[26px]"
        >
          Fits the stack you already have
        </h2>
        <p className="m-0 text-base text-muted-text max-sm:text-[15px]">
          Open protocols, no lock-in, <a href={links.github}>MIT</a>
        </p>
      </div>
      <ul className="m-0 grid list-none grid-cols-3 gap-4 p-0 max-md:grid-cols-1 max-sm:gap-[10px]">
        {ecosystem.map((e) => (
          <li key={e.title}>
            <a
              href={e.href}
              className="flex h-full flex-col gap-[10px] rounded-lg border border-border bg-surface p-[26px] text-ink-2 transition-colors hover:border-accent hover:text-ink-2 max-sm:gap-1.5 max-sm:p-[18px]"
            >
              <span className="font-mono text-[13px] text-accent max-sm:text-xs">{e.tag}</span>
              <h3 className="m-0 text-xl font-semibold leading-[1.3] text-heading max-sm:text-lg max-sm:leading-[1.3]">
                {e.title}
              </h3>
              <p className="m-0 text-base leading-[1.55] text-ink-2 max-sm:text-[15px] max-sm:leading-[1.5]">
                {e.desc}
              </p>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Surfaces() {
  return (
    <section
      className={cn(wrap, "pb-18 pt-22 max-md:pb-12 max-md:pt-16 max-sm:pb-9 max-sm:pt-12")}
      aria-labelledby="surfaces-title"
    >
      <div className="mb-8 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 max-sm:mb-4 max-sm:flex-col max-sm:items-start max-sm:gap-4">
        <h2
          id="surfaces-title"
          className="m-0 text-[34px] font-semibold leading-[1.2] tracking-heading text-heading max-sm:text-[26px]"
        >
          Run it your way
        </h2>
        <p className="m-0 text-base text-muted-text max-sm:text-[15px]">Same agent and config on every surface</p>
      </div>
      <ul className="m-0 grid list-none grid-cols-3 gap-4 p-0 max-md:grid-cols-2 max-sm:grid-cols-1 max-sm:gap-[10px]">
        {surfaces.map((s) => (
          <li key={s.title}>
            <a
              href={s.href}
              className="flex h-full flex-col gap-[10px] rounded-lg border border-border bg-surface p-[26px] text-ink-2 transition-colors hover:border-accent hover:text-ink-2 max-sm:gap-1.5 max-sm:p-[18px]"
            >
              <span className="font-mono text-[13px] text-accent max-sm:text-xs">{s.tag}</span>
              <h3 className="m-0 text-xl font-semibold leading-[1.3] text-heading max-sm:text-lg max-sm:leading-[1.3]">
                {s.title}
              </h3>
              <p className="m-0 text-base leading-[1.55] text-ink-2 max-sm:text-[15px] max-sm:leading-[1.5]">
                {s.desc}
              </p>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Stats({ stats }: { stats: SiteStats }) {
  const items: Array<[string, string]> = [
    ["GitHub stars", stats.starsLabel],
    ["Contributors", String(stats.contributors)],
    ["License", "MIT"],
    ["First commit", "2023"],
  ];
  return (
    <section
      className={cn(wrap, "flex flex-col gap-[14px] pb-18 max-md:pb-12 max-sm:gap-[10px] max-sm:pb-10")}
      aria-label="Project stats"
    >
      <dl className="m-0 grid grid-cols-4 gap-4 border-y border-border py-9 max-md:grid-cols-2 max-md:gap-y-8 max-sm:gap-x-3 max-sm:gap-y-5 max-sm:py-6">
        {items.map(([label, value]) => (
          <div key={label} className="flex flex-col-reverse justify-end gap-1 max-sm:gap-0.5">
            <dt className="text-[15px] text-muted-text max-sm:text-sm">{label}</dt>
            <dd className="m-0 font-mono text-[42px] font-semibold leading-[1.25] text-heading max-sm:text-[32px] max-sm:leading-[1.25]">
              {value}
            </dd>
          </div>
        ))}
      </dl>
      <p className="m-0 text-[13px] text-muted-text max-sm:text-xs">
        Stars and contributors update daily from <a href={links.stats}>gptme/stats</a>.
      </p>
    </section>
  );
}

const cta =
  "inline-flex items-center justify-center self-start whitespace-nowrap rounded-md px-[22px] py-[15px] text-base font-semibold max-sm:min-h-12 max-sm:w-full max-sm:px-3 max-sm:py-0";

function TwoWays() {
  return (
    <div className={cn(wrap, "grid grid-cols-2 gap-5 pb-22 max-md:grid-cols-1 max-md:pb-16 max-sm:gap-3 max-sm:pb-12")}>
      <section className={cn(panel, "gap-4 max-sm:gap-3")} aria-labelledby="self-host-title">
        <p className={eyebrow}>Run it yourself</p>
        <h3
          id="self-host-title"
          className="m-0 text-[26px] font-semibold leading-[1.3] text-heading [text-wrap:balance] max-sm:text-[21px] max-sm:leading-[1.3]"
        >
          Your machine, your keys, your data.
        </h3>
        <p className={panelText}>
          One <code className="font-mono text-[15px]">pipx install gptme</code> and it is yours: MIT-licensed, no
          account needed, and a local model if you want nothing leaving the machine at all.
        </p>
        <a
          href={links.gettingStarted}
          className={cn(cta, "mt-auto bg-accent text-on-accent hover:text-on-accent hover:brightness-110")}
        >
          Get started
        </a>
      </section>

      <section className={cn(panel, "gap-4 max-sm:gap-3")} aria-labelledby="managed-title">
        <p className={cn(eyebrow, "text-muted-text")}>Or let us run it</p>
        <h3
          id="managed-title"
          className="m-0 text-[26px] font-semibold leading-[1.3] text-heading [text-wrap:balance] max-sm:text-[21px] max-sm:leading-[1.3]"
        >
          gptme.ai runs the same agent, hosted.
        </h3>
        <p className={panelText}>
          Sign in and go: no install, no provider keys of your own, GitHub connected, same tools. Or keep running gptme
          locally and use it <a href={links.cloudDocs}>only for model access</a>. In early access.
        </p>
        <a
          href={links.gptmeAi}
          className={cn(cta, "mt-auto border border-border bg-bg text-ink hover:border-muted hover:text-ink")}
        >
          Go to gptme.ai →
        </a>
      </section>
    </div>
  );
}

export function Home({ stats }: { stats: SiteStats }) {
  return (
    <>
      <Hero />
      <SeeItWork />
      <Agents />
      <Surfaces />
      <Models />
      <Ecosystem />
      <Stats stats={stats} />
      <TwoWays />
    </>
  );
}
