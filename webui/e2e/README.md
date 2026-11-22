# Web UI e2e: streaming stability, scroll growth, and rrweb replay

`turn-stability.spec.ts` guards the streamed-turn behaviour tracked in
[gptme/gptme#5023](https://github.com/gptme/gptme/issues/5023). Everything runs on
synthetic content against the dev server; nothing records or uploads real conversations.

## What each oracle decides

| Oracle                                                                        | Where                                             | Decides                                                                                                                                                            |
| ----------------------------------------------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Frame sampler (`helpers/streamFrameOracle.ts`)                                | live page, one sample per `requestAnimationFrame` | **pass/fail**: no raw native-call header/argument marker in _visible_ text, painted reply tail stays visible and unclipped, fenced literal `@...` example survives |
| Disclosure scroll budgets (`opening tool output keeps scroll growth bounded`) | live `message-scroll-viewport` geometry           | **pass/fail**: default → expand group → open one output → collapse, short vs. thousands of lines, desktop and narrow                                               |
| Pixel probe (`helpers/pixelProbe.ts`)                                         | screenshot of where the tail should be            | **pass/fail** for transparent text / click-through overlays that DOM geometry cannot see                                                                           |
| rrweb `Replayer` (`helpers/rrwebReplay.ts`)                                   | reconstructed DOM in a second page                | **cross-check** that a recording reproduces the live verdicts; not a layout oracle                                                                                 |

Each negative control (one-frame payload flash, 4px-clipped tail, unbounded output pane,
transparent text, overlay) must be caught, so the oracles are shown able to fail.

### Sampling limits

A `requestAnimationFrame` sample sees what the previous frame painted plus synchronous
changes since. It can miss state that a task paints and reverts between two frames, or that
a later rAF/`ResizeObserver` callback changes before paint. It is not compositor-frame
capture. A `MutationObserver` records text entering the DOM as non-failing supplementary
evidence. Failure output carries the chunk index, phase, timestamp, text excerpt and clip
reason; Playwright adds a screenshot of the final page state (`screenshot: 'only-on-failure'`)
and a trace on the first CI retry.

## rrweb evaluation (rrweb 2.x, devDependency, test-only)

Recording is opt-in: `RRWEB_RECORD=1 npx playwright test e2e/turn-stability.spec.ts`
attaches `rrweb-events.json` to the streaming tests. The two `rrweb Replayer` tests
always record, because replaying is their purpose. No recorder or `rrweb` import is
reachable from `src/`.

**What it captured on the same fixtures**

- A one-frame payload flash as a separate add batch and remove batch ~17 ms apart; in the
  `Replayer` it reproduces in the same phase the live oracle flagged (chunk markers are
  embedded as custom events).
- A tail clip as a `style` attribute mutation (`max-height: 4px; overflow: hidden`).
- A pre-fix partial-header leak, but as fragmented text-node adds (`goldfish.search` +
  `<em>memo`): searching the raw events for `@goldfish` finds nothing. Replaying is needed.
- The disclosure click sequence: replayed `scrollHeight`, open-output count and block count
  match the live values per stage, so the live budgets run unchanged on the replayed DOM.

**What it does not do**

- No visibility: closed-`<details>` content serializes like visible text.
- No geometry or pixels: clipping/occlusion that does not change the DOM is invisible to it
  (hence the pixel probe, which runs on the live page).
- Mutations are batched per observer callback, so sub-frame ordering is approximate; timing
  of replayed faults is matched by phase, not wall-clock.

**Cost**: 210–290 KB per streaming recording, ~750 KB for the 4000-line disclosure
recording (short-output variant 240 KB). About 170 lines of helper code. Default CI runs
only the three small replay/pixel tests (~10 s) and keeps no recording artifacts.

**Verdict**: adopted as an opt-in/diagnostic and cross-check artifact (scrub a failing
stream in the `Replayer` to see which batch introduced the state). Pass/fail stays with the
frame sampler, geometry budgets and pixel probe.
