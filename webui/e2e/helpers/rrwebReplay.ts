import { join } from 'node:path';
import type { BrowserContext, JSHandle, Page } from '@playwright/test';

// rrweb record + real-Replayer helpers (gptme/gptme#5023).
//
// rrweb is a devDependency used only by e2e specs. A recording is the event
// stream of the synthetic test page; replaying it in rrweb's own `Replayer`
// (a sandboxed iframe in a second page) lets a test assert on the *reconstructed*
// DOM at any point in the timeline, independent of the live frame oracle. That
// validates the recording is a faithful replay artifact, not just serialized JSON.
//
// Limits: rrweb replays DOM mutations, attributes, scroll and input, not the
// compositor. Pass/fail for clicks and streaming therefore uses both the live
// geometry/frame oracles and the replayed DOM, never the replay alone.

const RRWEB_DIST = join(process.cwd(), 'node_modules', 'rrweb', 'dist');
const UMD = process.env.RRWEB_UMD ?? join(RRWEB_DIST, 'rrweb.umd.min.cjs');
const REPLAYER_CSS = join(RRWEB_DIST, 'style.min.css');

/** rrweb `EventType`: 3 = IncrementalSnapshot, 5 = Custom. */
const INCREMENTAL = 3;
const CUSTOM = 5;

export interface RrwebEvent {
  type: number;
  timestamp: number;
  data: { source?: number; tag?: string; payload?: Record<string, unknown> } & Record<
    string,
    unknown
  >;
}

export interface ReplayMarker {
  /** ms since the first event, comparable to `seek()` offsets. */
  offset: number;
  tag: string;
  payload: Record<string, unknown>;
}

type RecorderWindow = {
  rrweb: {
    record: ((o: object) => () => void) & {
      addCustomEvent: (tag: string, payload: object) => void;
    };
  };
  __rrweb?: { events: unknown[]; stop: () => void };
};

/** Start recording the page; rrweb takes a full snapshot of the current DOM. */
export async function startRecording(page: Page): Promise<void> {
  await page.addScriptTag({ path: UMD });
  await page.evaluate(() => {
    const w = window as unknown as RecorderWindow;
    const events: unknown[] = [];
    const stop = w.rrweb.record({ emit: (event: unknown) => events.push(event) });
    w.__rrweb = { events, stop };
  });
}

/** Embed a labelled marker in the event timeline (no-op when not recording). */
export async function markRecording(page: Page, tag: string, payload: object): Promise<void> {
  await page.evaluate(
    ({ tag, payload }) => {
      const w = window as unknown as RecorderWindow;
      if (w.__rrweb) w.rrweb.record.addCustomEvent(tag, payload);
    },
    { tag, payload }
  );
}

export async function stopRecording(page: Page): Promise<RrwebEvent[]> {
  return page.evaluate(() => {
    const w = window as unknown as RecorderWindow;
    w.__rrweb?.stop();
    return (w.__rrweb?.events ?? []) as unknown as RrwebEvent[];
  });
}

export function replayMarkers(events: RrwebEvent[]): ReplayMarker[] {
  const base = events[0].timestamp;
  return events
    .filter((e) => e.type === CUSTOM)
    .map((e) => ({
      offset: e.timestamp - base,
      tag: String(e.data.tag),
      payload: e.data.payload ?? {},
    }));
}

/** Offsets (ms) of every DOM-changing event, i.e. every distinct replayable state. */
export function mutationOffsets(events: RrwebEvent[]): number[] {
  const base = events[0].timestamp;
  const offsets = events
    // source 0 = Mutation, 2 = MouseInteraction (click), 3 = Scroll
    .filter((e) => e.type === INCREMENTAL && [0, 2, 3].includes(Number(e.data.source)))
    .map((e) => e.timestamp - base);
  return [...new Set(offsets)].sort((a, b) => a - b);
}

export class ReplaySession {
  constructor(
    readonly page: Page,
    readonly events: RrwebEvent[]
  ) {}

  /** Seek the Replayer to `offset` ms and let it paint. */
  async seek(offset: number): Promise<void> {
    await this.page.evaluate(async (offset) => {
      const w = window as unknown as { __replayer: { pause: (t: number) => void } };
      w.__replayer.pause(offset);
      await new Promise<void>((r) => requestAnimationFrame(() => r()));
    }, offset);
  }

  /** Run `fn` inside the replayed document (the Replayer's iframe). */
  async inReplay<T, A>(fn: (doc: Document, arg: A) => T, arg: A): Promise<T> {
    type Run = (doc: Document, arg: A) => T;
    const handle = await this.page.evaluateHandle(
      () =>
        (window as unknown as { __replayer: { iframe: HTMLIFrameElement } }).__replayer.iframe
          .contentDocument!
    );
    try {
      return await (handle as JSHandle<Document>).evaluate(fn as Run as never, arg as never);
    } finally {
      await handle.dispose();
    }
  }

  async close(): Promise<void> {
    await this.page.close();
  }
}

/**
 * Load `events` into rrweb's actual `Replayer` in a fresh page. The Replayer
 * rebuilds the recorded DOM inside a sandboxed same-origin iframe at the
 * recorded viewport size.
 */
export async function openReplayer(
  context: BrowserContext,
  events: RrwebEvent[]
): Promise<ReplaySession> {
  const page = await context.newPage();
  await page.setViewportSize({ width: 1500, height: 1100 });
  await page.setContent('<!doctype html><body style="margin:0"><div id="root"></div></body>');
  await page.addScriptTag({ path: UMD });
  await page.addStyleTag({ path: REPLAYER_CSS });
  await page.evaluate((events) => {
    const rrweb = (
      window as unknown as {
        rrweb: { Replayer: new (events: unknown[], config: object) => unknown };
      }
    ).rrweb;
    const replayer = new rrweb.Replayer(events, {
      root: document.getElementById('root')!,
      skipInactive: false,
      showWarning: false,
      mouseTail: false,
      // Apply mutations straight to the real DOM so a seek is synchronous.
      useVirtualDom: false,
    });
    Object.assign(window, { __replayer: replayer });
  }, events);
  return new ReplaySession(page, events);
}
