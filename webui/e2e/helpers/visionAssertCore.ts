/**
 * Opt-in vision-model assertion core (no Playwright dependency).
 *
 * The Playwright wrapper lives in ./visionAssert.ts. This module is unit-tested
 * by Jest and can be called from a one-off script.
 *
 * Enable with GPTME_VISION_ASSERT=1. Never turn this on for every CI run until
 * cost, latency, and false-positive rate are understood.
 *
 * Cost / latency / secrets (recorded 2026-08-23, before any always-on workflow):
 *   - Secret: OPENROUTER_API_KEY, or GPTME_VISION_API_KEY to override.
 *   - Default model: openrouter/deepseek/deepseek-v4-flash-vision-exp@deepseek via OpenRouter.
 *   - ~1/5th the price of gemini-2.5-flash and slightly faster (Erik 2026-08-25).
 *   - Measured 2026-08-23 on google/gemini-2.5-flash (old default): a 220x30
 *     cropped PNG used ~$0.00087 / 5.6s; a 1000x80 PNG used ~$0.00116 / 1.0s.
 *     Budget about $0.0002–$0.002 and ~1–4s per assertion (timeout 30s).
 *   - Failure mode: the model can hallucinate "pass" or "fail"; treat this as a
 *     second opinion on top of Playwright/DOM assertions, not a replacement.
 */

export const DEFAULT_VISION_MODEL = 'openrouter/deepseek/deepseek-v4-flash-vision-exp@deepseek';
export const OPENROUTER_CHAT_URL = 'https://openrouter.ai/api/v1/chat/completions';
export const VISION_ASSERT_TIMEOUT_MS = 30_000;

export type VisionVerdict = {
  pass: boolean;
  reason: string;
};

export function isVisionAssertEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return env.GPTME_VISION_ASSERT === '1';
}

export function resolveVisionApiKey(env: NodeJS.ProcessEnv = process.env): string | undefined {
  const key = env.GPTME_VISION_API_KEY || env.OPENROUTER_API_KEY;
  return key && key.trim() ? key.trim() : undefined;
}

export function buildVisionAssertPrompt(claim: string): string {
  const trimmed = claim.trim();
  if (!trimmed) {
    throw new Error('vision_assert claim must be a non-empty string');
  }
  return `You are a strict visual QA judge for a web UI screenshot.
Inspect ONLY pixels that are actually rendered in the image. Do not assume the
claim is true because it was written here. CSS/DOM presence is irrelevant.

Claim to verify:
${trimmed}

Rules:
- PASS only if the claim is clearly and completely true in the screenshot.
- FAIL if the relevant content is clipped, truncated, covered by another element,
  off-screen, unreadable, or absent.
- One narrow claim: do not invent extra requirements.

Return JSON only with this exact shape:
{"pass": true or false, "reason": "one sentence citing visible evidence"}`;
}

export function parseVisionVerdict(raw: string): VisionVerdict {
  if (typeof raw !== 'string' || !raw.trim()) {
    throw new Error('vision_assert model returned empty content');
  }

  const jsonText = extractJsonObject(raw);
  let parsed: unknown;
  try {
    parsed = JSON.parse(jsonText);
  } catch (err) {
    throw new Error(`vision_assert model returned invalid JSON: ${(err as Error).message}`);
  }

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('vision_assert verdict is not a JSON object');
  }
  const obj = parsed as Record<string, unknown>;
  if (typeof obj.pass !== 'boolean') {
    throw new Error('vision_assert verdict.pass must be a boolean');
  }
  if (typeof obj.reason !== 'string' || !obj.reason.trim()) {
    throw new Error('vision_assert verdict.reason must be a non-empty string');
  }
  return { pass: obj.pass, reason: obj.reason.trim() };
}

export async function requestVisionVerdict(opts: {
  imagePng: Buffer;
  claim: string;
  apiKey: string;
  model?: string;
  fetchImpl?: typeof fetch;
}): Promise<VisionVerdict> {
  if (!opts.imagePng || opts.imagePng.length === 0) {
    throw new Error('vision_assert screenshot is empty');
  }
  if (!opts.apiKey.trim()) {
    throw new Error('vision_assert API key is empty');
  }

  const model = opts.model || process.env.GPTME_VISION_MODEL || DEFAULT_VISION_MODEL;
  const fetchImpl = opts.fetchImpl ?? globalThis.fetch;
  if (!fetchImpl) {
    throw new Error('vision_assert requires fetch() (Node 18+)');
  }

  const prompt = buildVisionAssertPrompt(opts.claim);
  const encoded = opts.imagePng.toString('base64');
  const payload = {
    model,
    messages: [
      {
        role: 'user',
        content: [
          { type: 'text', text: prompt },
          {
            type: 'image_url',
            image_url: { url: `data:image/png;base64,${encoded}` },
          },
        ],
      },
    ],
    response_format: { type: 'json_object' },
    temperature: 0,
    max_tokens: 400,
  };

  let lastError: Error | undefined;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    let response: Response;
    try {
      response = await fetchImpl(OPENROUTER_CHAT_URL, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${opts.apiKey}`,
          'Content-Type': 'application/json',
          'HTTP-Referer': 'https://github.com/gptme/gptme',
          'X-Title': 'gptme-webui-vision-assert',
        },
        body: JSON.stringify(payload),
        // jsdom's AbortSignal has no .timeout(); Node 18+/Playwright CI does.
        ...(typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
          ? { signal: AbortSignal.timeout(VISION_ASSERT_TIMEOUT_MS) }
          : {}),
      });
    } catch (err) {
      // Network-level failure (timeout, DNS, connection reset): retry once.
      lastError = err as Error;
      continue;
    }
    if (!response.ok) {
      const body = await response.text().catch(() => '');
      const msg = `vision_assert OpenRouter HTTP ${response.status}: ${body.slice(0, 500)}`;
      if (response.status >= 500) {
        // Server error: retry once.
        lastError = new Error(msg);
        continue;
      }
      // Client error (4xx): fail immediately — retrying won't help.
      throw new Error(msg);
    }
    try {
      const data: unknown = await response.json();
      const content = extractChoiceContent(data);
      return parseVisionVerdict(content);
    } catch (err) {
      lastError = err as Error;
    }
  }
  throw lastError ?? new Error('vision_assert failed to parse model response');
}

function extractChoiceContent(data: unknown): string {
  if (!data || typeof data !== 'object') {
    throw new Error('vision_assert OpenRouter response is not an object');
  }
  const choices = (data as { choices?: unknown }).choices;
  if (!Array.isArray(choices) || choices.length === 0) {
    throw new Error('vision_assert OpenRouter response has no choices');
  }
  const content = (choices[0] as { message?: { content?: unknown } })?.message?.content;
  if (typeof content !== 'string') {
    throw new Error('vision_assert OpenRouter response lacks string content');
  }
  return content;
}

function extractJsonObject(raw: string): string {
  const trimmed = raw.trim();
  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  const candidate = (fenced ? fenced[1] : trimmed).trim();

  // Fast path: candidate is already valid JSON (common when the model returns
  // just the object with no surrounding prose).
  try {
    JSON.parse(candidate);
    return candidate;
  } catch {
    // fall through to string-aware extraction
  }

  // Try each '{' position in the candidate. If the model adds prose containing
  // a '{' before the actual JSON object (e.g. "Some text { brace } then {...}"),
  // the first match may produce invalid JSON — try successive positions until
  // one yields a parseable object.
  //
  // The inner scanner is string-aware so that '}' inside a string literal (e.g.
  // {"pass": false, "reason": "clipped by }"}) does not prematurely close the
  // depth counter.
  let searchFrom = 0;
  while (true) {
    const start = candidate.indexOf('{', searchFrom);
    if (start === -1) break;

    let depth = 0;
    let inString = false;
    let i = start;
    let end = -1;
    while (i < candidate.length) {
      const ch = candidate[i];
      if (inString) {
        if (ch === '\\') {
          i += 2; // skip escaped character (e.g. \" \\ \n)
          continue;
        }
        if (ch === '"') inString = false;
      } else {
        if (ch === '"') inString = true;
        else if (ch === '{') depth++;
        else if (ch === '}') {
          depth--;
          if (depth === 0) {
            end = i;
            break;
          }
        }
      }
      i++;
    }

    if (end !== -1) {
      const slice = candidate.slice(start, end + 1);
      try {
        JSON.parse(slice);
        return slice;
      } catch {
        // This '{...}' block was not valid JSON; try the next '{' in the candidate.
      }
    }

    searchFrom = start + 1;
  }
  throw new Error('vision_assert model response contains no JSON object');
}
