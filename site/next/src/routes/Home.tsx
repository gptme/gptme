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
  { tag: "gptme.vim", title: "Vim", desc: "Talk to the agent without leaving your editor.", href: links.vim },
  { tag: "rest · mcp · acp", title: "Server", desc: "A REST API, plus MCP and ACP in both directions.", href: links.server },
];

// The last tool is dropped on narrow screens (MainMobile design board).
const tools = ["shell", "ipython", "patch", "browser", "vision", "computer", "subagent"];

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
          gptme runs anywhere a terminal runs: your laptop, ssh sessions, tmux, headless servers, CI pipelines. Bring
          Claude, GPT, Gemini, Grok, DeepSeek, or a local model.
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

function SeeItWork() {
  return (
    <section className="border-y border-border bg-surface" aria-labelledby="demo-title">
      <div
        className={cn(
          wrap,
          "grid grid-cols-[5fr_7fr] items-center gap-16 py-22",
          "max-md:grid-cols-1 max-md:gap-10 max-md:py-16 max-sm:gap-[18px] max-sm:py-12",
        )}
      >
        <div className="flex min-w-0 flex-col gap-5 max-sm:gap-[18px]">
          <p className={eyebrow}>See it work</p>
          <h2
            id="demo-title"
            className="m-0 text-[38px] font-semibold leading-[1.18] tracking-heading text-heading [text-wrap:balance] max-sm:text-[27px] max-sm:leading-[1.2]"
          >
            It runs the command, reads the error, and fixes it.
          </h2>
          <p className="m-0 text-lg leading-[1.6] text-ink-2 [text-wrap:pretty] max-sm:text-base max-sm:leading-[1.6]">
            Tool output goes back to the model, so gptme corrects itself instead of guessing.
            <span className={wide}>
              {" "}
              It works with your shell, Python, files, the browser, images and the full desktop.
            </span>
          </p>
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0 max-sm:gap-1.5" aria-label="Built-in tools">
            {tools.map((t, i) => (
              <li
                key={t}
                className={cn(
                  "rounded-pill bg-chip px-3 py-1.5 font-mono text-[13px] text-ink-2 max-sm:px-[10px] max-sm:py-[5px] max-sm:text-xs",
                  i === tools.length - 1 && wide,
                )}
              >
                {t}
              </li>
            ))}
          </ul>
        </div>
        <Terminal />
      </div>
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

const panel =
  "flex min-w-0 flex-col gap-[14px] rounded-lg border border-border bg-surface p-8 max-sm:gap-[10px] max-sm:p-[22px]";
const panelText = "m-0 text-[17px] leading-[1.6] text-ink-2 max-sm:text-[15px] max-sm:leading-[1.6]";

function BobAndManaged() {
  return (
    <div className={cn(wrap, "grid grid-cols-2 gap-5 pb-22 max-md:grid-cols-1 max-md:pb-16 max-sm:gap-3 max-sm:pb-12")}>
      <section className={panel} aria-labelledby="bob-title">
        <p className={eyebrow}>Built with gptme</p>
        <h3
          id="bob-title"
          className="m-0 text-[26px] font-semibold leading-[1.3] text-heading [text-wrap:balance] max-sm:text-[21px] max-sm:leading-[1.3]"
        >
          Bob is an autonomous agent running on gptme, 24/7.
        </h3>
        <p className={panelText}>
          Forked from <a href={links.agentTemplate}>gptme-agent-template</a>, he has merged 4,000+ public pull requests,
          many of them into gptme itself.
        </p>
        <a
          href={links.bobTimeline}
          className="text-base font-medium text-accent hover:text-accent-2-text max-sm:text-[15px]"
        >
          See Bob's timeline →
        </a>
      </section>

      <section className={cn(panel, "gap-[18px] max-sm:gap-[14px]")} aria-labelledby="compare-title">
        <p id="compare-title" className={cn(eyebrow, "text-muted-text")}>
          Open source or managed
        </p>
        {[
          ["gptme", links.github, "You run it. MIT-licensed, your machine, your keys or a local model."],
          ["gptme.ai", links.gptmeAi, "We run it for you. Managed instances, nothing to set up."],
        ].map(([name, href, desc], i) => (
          <div
            key={name}
            className={cn(
              "grid grid-cols-[120px_1fr] items-baseline gap-4 max-sm:grid-cols-1 max-sm:gap-1",
              i > 0 && "border-t border-border pt-[18px] max-sm:pt-[14px]",
            )}
          >
            <a
              href={href}
              className="font-mono text-xl font-semibold text-heading hover:text-accent max-sm:text-lg"
            >
              {name}
            </a>
            <p className={cn(panelText, "leading-[1.55] max-sm:leading-[1.55]")}>{desc}</p>
          </div>
        ))}
      </section>
    </div>
  );
}

export function Home({ stats }: { stats: SiteStats }) {
  return (
    <>
      <Hero />
      <SeeItWork />
      <Surfaces />
      <Stats stats={stats} />
      <BobAndManaged />
    </>
  );
}
