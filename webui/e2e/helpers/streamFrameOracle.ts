import type { Page } from '@playwright/test';

// In-page frame oracle for streamed replies (gptme/gptme#5023).
//
// Samples the message once per animation frame, while the stream runs, instead
// of asserting only after the stream finishes. Each sample records the chunk index
// and phase that the test last applied, so a failure can be traced to the
// split that caused it.
//
// Sampling limits: a rAF callback sees the DOM that the *previous* frame painted,
// plus any synchronous changes made since then. It misses state that a task
// paints and reverts between two frames without a rAF in between. It also misses
// state that a later rAF/ResizeObserver callback changes before paint. A
// MutationObserver supplies supplementary, non-failing evidence of leak text that
// enters the DOM even if it was never painted. This is not compositor-frame
// capture.

export interface OracleConfig {
  messageSelector: string;
  /** Full prose tail that must stay visible and unclipped once painted. */
  tail: string;
  /**
   * Literal fenced example of native syntax (with a different tool than the
   * streamed call) that must stay visible: a negative control against an
   * implementation that passes by hiding all `@...` text.
   */
  literal?: string;
  /** Substrings that must never be visible in the collapsed message. */
  leakMarkers: string[];
  /** Regex source; a visible match is a raw-header leak (e.g. `@sh` partial header). */
  leakPattern?: string;
}

export interface FrameSample {
  t: number;
  chunk: number;
  phase: string;
  leak: string | null;
  tailPainted: boolean;
  tailVisible: boolean;
  tailClip: string | null;
  literalVisible: boolean | null;
  fault: string | null;
  excerpt: string;
}

export interface OracleResult {
  samples: FrameSample[];
  failures: FrameSample[];
  phases: string[];
  mutationLeaks: { t: number; chunk: number; text: string }[];
}

export type Fault = 'payload-flash' | 'tail-clip';

export async function installFrameOracle(page: Page, config: OracleConfig) {
  await page.evaluate((config) => {
    type Sample = {
      t: number;
      chunk: number;
      phase: string;
      leak: string | null;
      tailPainted: boolean;
      tailVisible: boolean;
      tailClip: string | null;
      literalVisible: boolean | null;
      fault: string | null;
      excerpt: string;
    };
    const state = {
      running: true,
      chunk: -1,
      phase: 'init',
      tailPainted: false,
      literalPainted: false,
      samples: [] as Sample[],
      mutationLeaks: [] as { t: number; chunk: number; text: string }[],
      // Controlled faults: applied for exactly one painted frame.
      pendingFault: null as string | null,
      activeFault: null as null | { kind: string; undo: () => void },
    };
    const leakPattern = config.leakPattern ? new RegExp(config.leakPattern) : null;

    const findLeak = (text: string): string | null => {
      for (const marker of config.leakMarkers) if (text.includes(marker)) return marker;
      const match = leakPattern?.exec(text);
      return match ? match[0] : null;
    };

    // Range over the last characters of the tail. A streaming parser appends one
    // text node per chunk, so map an offset in the concatenated text back to nodes.
    const tailRange = (message: HTMLElement): Range | null => {
      const needle = config.tail.slice(-12);
      const walker = document.createTreeWalker(message, NodeFilter.SHOW_TEXT);
      const nodes: { node: Text; start: number }[] = [];
      let text = '';
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        nodes.push({ node: node as Text, start: text.length });
        text += node.textContent ?? '';
      }
      const at = text.lastIndexOf(needle);
      if (at < 0) return null;
      const locate = (offset: number, end: boolean) => {
        for (let i = nodes.length - 1; i >= 0; i--) {
          const { node, start } = nodes[i];
          if (end ? offset > start : offset >= start) return { node, offset: offset - start };
        }
        return null;
      };
      const from = locate(at, false);
      const to = locate(at + needle.length, true);
      if (!from || !to) return null;
      const range = document.createRange();
      range.setStart(from.node, from.offset);
      range.setEnd(to.node, to.offset);
      return range;
    };

    // Geometry: the tail must have area and lie inside every clipping ancestor
    // up to and including the scroll viewport. Then the point at its centre
    // must hit the message, which detects overlays/occlusion.
    const tailClip = (message: HTMLElement): string | null => {
      const range = tailRange(message);
      if (!range) return 'tail text node missing';
      const rect = range.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return `tail has no area ${rect.width}x${rect.height}`;
      for (let el = range.startContainer.parentElement; el; el = el.parentElement) {
        const style = getComputedStyle(el);
        const clips =
          style.overflowX !== 'visible' ||
          style.overflowY !== 'visible' ||
          style.clipPath !== 'none';
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
          return `ancestor <${el.tagName.toLowerCase()}> hides tail`;
        }
        if (clips) {
          const box = el.getBoundingClientRect();
          const tol = 1;
          if (
            rect.top < box.top - tol ||
            rect.bottom > box.bottom + tol ||
            rect.left < box.left - tol ||
            rect.right > box.right + tol
          ) {
            return (
              `clipped by <${el.tagName.toLowerCase()}${el.className ? '.' + String(el.className).split(' ')[0] : ''}>` +
              ` tail=${Math.round(rect.top)}..${Math.round(rect.bottom)}` +
              ` box=${Math.round(box.top)}..${Math.round(box.bottom)}`
            );
          }
        }
        if (el.dataset.testid === 'message-scroll-viewport') break;
      }
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      if (!hit || !message.contains(hit)) {
        return `occluded by <${hit?.tagName.toLowerCase() ?? 'nothing'}>`;
      }
      return null;
    };

    const applyFault = (message: HTMLElement, kind: string) => {
      if (kind === 'payload-flash') {
        const span = document.createElement('span');
        span.textContent = '@shell(call_fault): {"command":"flash"}';
        message.appendChild(span);
        return () => span.remove();
      }
      // tail-clip: the reply box collapses to a sliver for one frame.
      const target = message.querySelector<HTMLElement>('.chat-message, .markdown') ?? message;
      const previous = target.getAttribute('style');
      target.style.maxHeight = '4px';
      target.style.overflow = 'hidden';
      return () => {
        if (previous === null) target.removeAttribute('style');
        else target.setAttribute('style', previous);
      };
    };

    const sample = () => {
      if (!state.running) return;
      const message = document.querySelector<HTMLElement>(config.messageSelector);
      // innerText omits closed <details> content and display:none subtrees.
      const text = message?.innerText ?? '';
      const tailVisible = text.includes(config.tail);
      if (tailVisible) state.tailPainted = true;
      const literalVisible = config.literal ? text.includes(config.literal) : null;
      if (literalVisible) state.literalPainted = true;
      const fault = state.activeFault?.kind ?? null;
      state.samples.push({
        t: performance.now(),
        chunk: state.chunk,
        phase: state.phase,
        leak: findLeak(text),
        tailPainted: state.tailPainted,
        tailVisible,
        tailClip: message && state.tailPainted ? tailClip(message) : null,
        // Null until painted once; afterwards it must stay visible.
        literalVisible: state.literalPainted ? literalVisible : null,
        fault,
        // Tail of the visible text, for diagnosing a failing frame.
        excerpt: text.slice(-160),
      });
      // Undo after the faulty frame has been painted and sampled.
      if (state.activeFault) {
        state.activeFault.undo();
        state.activeFault = null;
      } else if (state.pendingFault && message) {
        state.activeFault = {
          kind: state.pendingFault,
          undo: applyFault(message, state.pendingFault),
        };
        state.pendingFault = null;
      }
      requestAnimationFrame(sample);
    };

    const observer = new MutationObserver((records) => {
      for (const record of records) {
        const nodes = record.type === 'characterData' ? [record.target] : [...record.addedNodes];
        for (const node of nodes) {
          const leak = findLeak(node.textContent ?? '');
          if (leak) {
            state.mutationLeaks.push({ t: performance.now(), chunk: state.chunk, text: leak });
          }
        }
      }
    });
    const root = document.querySelector('[data-testid="message-scroll-viewport"]') ?? document.body;
    observer.observe(root, { subtree: true, childList: true, characterData: true });
    Object.assign(window, { __frameOracle: { state, observer } });
    requestAnimationFrame(sample);
  }, config);
}

type OracleWindow = {
  __frameOracle: {
    state: {
      running: boolean;
      chunk: number;
      phase: string;
      pendingFault: string | null;
      samples: FrameSample[];
      mutationLeaks: { t: number; chunk: number; text: string }[];
    };
    observer: MutationObserver;
  };
};

export async function markChunk(page: Page, chunk: number, phase: string) {
  await page.evaluate(
    ({ chunk, phase }) => {
      const { state } = (window as unknown as OracleWindow).__frameOracle;
      state.chunk = chunk;
      state.phase = phase;
    },
    { chunk, phase }
  );
}

/** Inject a one-painted-frame fault (oracle negative control). */
export async function injectFault(page: Page, kind: Fault) {
  await page.evaluate((kind) => {
    (window as unknown as OracleWindow).__frameOracle.state.pendingFault = kind;
  }, kind);
}

export async function stopFrameOracle(page: Page): Promise<OracleResult> {
  return page.evaluate(() => {
    const { state, observer } = (window as unknown as OracleWindow).__frameOracle;
    state.running = false;
    observer.disconnect();
    const failures = state.samples.filter(
      (s) =>
        s.leak !== null ||
        (s.tailPainted && !s.tailVisible) ||
        s.tailClip !== null ||
        s.literalVisible === false
    );
    return {
      samples: state.samples,
      failures,
      phases: [...new Set(state.samples.map((s) => s.phase))],
      mutationLeaks: state.mutationLeaks,
    };
  });
}

// Opt-in rrweb recording for diagnosis (gptme/gptme#5023 evaluation).
// Set RRWEB_UMD to a local rrweb UMD bundle (e.g. node_modules/rrweb/dist/rrweb.umd.min.cjs
// from a scratch install). It is not a dependency, never runs in default CI, and
// records only the synthetic test page into a local test artifact. Its event
// stream is diagnostic evidence; pass/fail stays with the frame oracle.
export async function startRrweb(page: Page): Promise<boolean> {
  const bundle = process.env.RRWEB_UMD;
  if (!bundle) return false;
  await page.addScriptTag({ path: bundle });
  await page.evaluate(() => {
    const events: unknown[] = [];
    const rrweb = (window as unknown as { rrweb: { record: (o: object) => () => void } }).rrweb;
    const stop = rrweb.record({ emit: (event: unknown) => events.push(event) });
    Object.assign(window, { __rrweb: { events, stop } });
  });
  return true;
}

export async function stopRrweb(page: Page): Promise<unknown[]> {
  return page.evaluate(() => {
    const state = (window as unknown as { __rrweb?: { events: unknown[]; stop: () => void } })
      .__rrweb;
    if (!state) return [];
    state.stop();
    return state.events;
  });
}
