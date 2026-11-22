import type { Page } from '@playwright/test';

// Pixel evidence for clipped/occluded/invisible text (gptme/gptme#5023).
//
// The frame oracle decides visibility from geometry (rects, overflow ancestors,
// elementFromPoint). That cannot see a pointer-events:none overlay or
// transparent text: the box is where it should be but nothing is painted. This
// probe screenshots the region where the text *should* be and counts pixels
// that differ from the region's own background. Decoding happens in the page
// (createImageBitmap + canvas), so no image dependency is needed.

export interface Clip {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface InkResult {
  /** Pixels clearly different from the region's dominant (background) colour. */
  ink: number;
  total: number;
  /** Dominant colour as `rgb(r, g, b)`: usable to paint a same-colour overlay. */
  background: string;
}

/** Viewport rect of the last occurrence of `needle` inside `selector`, scrolled into view. */
export async function textClip(page: Page, selector: string, needle: string): Promise<Clip | null> {
  return page.evaluate(
    ({ selector, needle }) => {
      const root = document.querySelector<HTMLElement>(selector);
      if (!root) return null;
      // The stream appends one text node per chunk: map the needle's offset in
      // the concatenated text back to (node, offset) pairs.
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
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
      from.node.parentElement!.scrollIntoView({ block: 'center' });
      const range = document.createRange();
      range.setStart(from.node, from.offset);
      range.setEnd(to.node, to.offset);
      const rect = range.getBoundingClientRect();
      const x = Math.max(0, Math.floor(rect.left));
      const y = Math.max(0, Math.floor(rect.top));
      return {
        x,
        y,
        width: Math.ceil(rect.right) - x,
        height: Math.ceil(rect.bottom) - y,
      };
    },
    { selector, needle }
  );
}

/** Count ink pixels in `clip` of the current page. */
export async function inkPixels(page: Page, clip: Clip): Promise<InkResult> {
  const png = (await page.screenshot({ clip })).toString('base64');
  return page.evaluate(async (png) => {
    const bytes = Uint8Array.from(atob(png), (c) => c.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const ctx = canvas.getContext('2d')!;
    ctx.drawImage(bitmap, 0, 0);
    const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const counts = new Map<number, number>();
    for (let i = 0; i < data.length; i += 4) {
      const key = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    let background = 0;
    let best = -1;
    for (const [key, count] of counts) {
      if (count > best) {
        best = count;
        background = key;
      }
    }
    const [br, bg, bb] = [(background >> 16) & 255, (background >> 8) & 255, background & 255];
    let ink = 0;
    for (let i = 0; i < data.length; i += 4) {
      const delta =
        Math.abs(data[i] - br) + Math.abs(data[i + 1] - bg) + Math.abs(data[i + 2] - bb);
      if (delta > 150) ink++;
    }
    return { ink, total: data.length / 4, background: `rgb(${br}, ${bg}, ${bb})` };
  }, png);
}
