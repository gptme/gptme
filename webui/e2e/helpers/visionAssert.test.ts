import { describe, expect, it } from '@jest/globals';
import {
  DEFAULT_VISION_MODEL,
  OPENROUTER_CHAT_URL,
  buildVisionAssertPrompt,
  isVisionAssertEnabled,
  parseVisionVerdict,
  requestVisionVerdict,
  resolveVisionApiKey,
} from './visionAssertCore';

describe('visionAssertCore', () => {
  describe('isVisionAssertEnabled', () => {
    it('is off unless GPTME_VISION_ASSERT=1', () => {
      expect(isVisionAssertEnabled({})).toBe(false);
      expect(isVisionAssertEnabled({ GPTME_VISION_ASSERT: 'true' })).toBe(false);
      expect(isVisionAssertEnabled({ GPTME_VISION_ASSERT: '1' })).toBe(true);
    });
  });

  describe('resolveVisionApiKey', () => {
    it('prefers GPTME_VISION_API_KEY over OPENROUTER_API_KEY', () => {
      expect(
        resolveVisionApiKey({
          GPTME_VISION_API_KEY: 'vision-key',
          OPENROUTER_API_KEY: 'or-key',
        })
      ).toBe('vision-key');
      expect(resolveVisionApiKey({ OPENROUTER_API_KEY: 'or-key' })).toBe('or-key');
      expect(resolveVisionApiKey({})).toBeUndefined();
    });
  });

  describe('buildVisionAssertPrompt', () => {
    it('embeds the claim and asks for JSON', () => {
      const prompt = buildVisionAssertPrompt('assistant message is not clipped');
      expect(prompt).toContain('assistant message is not clipped');
      expect(prompt).toContain('"pass"');
      expect(prompt).toContain('clipped');
    });

    it('rejects an empty claim', () => {
      expect(() => buildVisionAssertPrompt('   ')).toThrow(/non-empty/);
    });
  });

  describe('parseVisionVerdict', () => {
    it('parses a bare JSON object', () => {
      expect(parseVisionVerdict('{"pass": true, "reason": "full text visible"}')).toEqual({
        pass: true,
        reason: 'full text visible',
      });
    });

    it('parses fenced JSON and surrounding prose', () => {
      const raw = 'Sure.\n```json\n{"pass": false, "reason": "message is clipped"}\n```\n';
      expect(parseVisionVerdict(raw)).toEqual({
        pass: false,
        reason: 'message is clipped',
      });
    });

    it('parses JSON when prose before it contains a bare brace', () => {
      // If the model writes "Some text { note } and then {...}", the first '{'
      // starts a non-JSON fragment; the scanner must skip it and find the real object.
      const raw = 'Some text { with a brace } and then {"pass": true, "reason": "ok"}';
      expect(parseVisionVerdict(raw)).toEqual({ pass: true, reason: 'ok' });
    });

    it('parses JSON followed by trailing prose containing a closing brace', () => {
      const raw = 'The result is {"pass": true, "reason": "visible"} and that\'s all.}';
      expect(parseVisionVerdict(raw)).toEqual({
        pass: true,
        reason: 'visible',
      });
    });

    it('parses JSON whose reason string contains a closing brace', () => {
      // The input must NOT be valid JSON overall so the fast path (JSON.parse)
      // fails and the string-aware brace scanner actually runs. A depth-only
      // scanner without quote awareness would close depth at the '}' inside the
      // string and return an invalid slice; the string-aware scanner must
      // recognise it as an in-string character and continue to the real end.
      const raw =
        'Here is the result: {"pass": false, "reason": "text cut off by }"} as requested.';
      expect(parseVisionVerdict(raw)).toEqual({
        pass: false,
        reason: 'text cut off by }',
      });
    });

    it('rejects missing or non-boolean pass', () => {
      expect(() => parseVisionVerdict('{"reason": "ok"}')).toThrow(/pass/);
      expect(() => parseVisionVerdict('{"pass": "true", "reason": "ok"}')).toThrow(/pass/);
    });

    it('rejects empty content', () => {
      expect(() => parseVisionVerdict('')).toThrow(/empty/);
    });
  });

  describe('requestVisionVerdict', () => {
    it('posts the screenshot and returns a parsed verdict', async () => {
      const fetchImpl = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          choices: [
            {
              message: {
                content: JSON.stringify({ pass: true, reason: 'echo text fully visible' }),
              },
            },
          ],
        }),
      });

      const verdict = await requestVisionVerdict({
        imagePng: Buffer.from('fake-png'),
        claim: 'assistant message is complete and not clipped',
        apiKey: 'test-key',
        fetchImpl: fetchImpl as unknown as typeof fetch,
      });

      expect(verdict).toEqual({ pass: true, reason: 'echo text fully visible' });
      expect(fetchImpl).toHaveBeenCalledTimes(1);
      const [url, init] = fetchImpl.mock.calls[0];
      expect(url).toBe(OPENROUTER_CHAT_URL);
      expect(init.method).toBe('POST');
      expect(init.headers.Authorization).toBe('Bearer test-key');
      expect(init.headers['HTTP-Referer']).toBe('https://github.com/gptme/gptme');
      const body = JSON.parse(init.body as string);
      expect(body.model).toBe(DEFAULT_VISION_MODEL);
      expect(body.temperature).toBe(0);
      expect(body.messages[0].content[1].image_url.url).toMatch(/^data:image\/png;base64,/);
    });

    it('retries once when the first response is not valid JSON', async () => {
      const fetchImpl = jest
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            choices: [{ message: { content: 'not-json' } }],
          }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            choices: [
              {
                message: { content: '{"pass": false, "reason": "text is cut off"}' },
              },
            ],
          }),
        });

      const verdict = await requestVisionVerdict({
        imagePng: Buffer.from('fake-png'),
        claim: 'message is not clipped',
        apiKey: 'test-key',
        fetchImpl: fetchImpl as unknown as typeof fetch,
      });

      expect(fetchImpl).toHaveBeenCalledTimes(2);
      expect(verdict.pass).toBe(false);
    });

    it('fails immediately on 4xx without retrying', async () => {
      const fetchImpl = jest.fn().mockResolvedValue({
        ok: false,
        status: 401,
        text: async () => 'unauthorized',
      });

      await expect(
        requestVisionVerdict({
          imagePng: Buffer.from('fake-png'),
          claim: 'visible',
          apiKey: 'bad-key',
          fetchImpl: fetchImpl as unknown as typeof fetch,
        })
      ).rejects.toThrow(/HTTP 401/);
      expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it('retries once on 5xx server error', async () => {
      const fetchImpl = jest
        .fn()
        .mockResolvedValueOnce({
          ok: false,
          status: 503,
          text: async () => 'Service Unavailable',
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            choices: [{ message: { content: '{"pass": true, "reason": "visible"}' } }],
          }),
        });

      const verdict = await requestVisionVerdict({
        imagePng: Buffer.from('fake-png'),
        claim: 'visible',
        apiKey: 'test-key',
        fetchImpl: fetchImpl as unknown as typeof fetch,
      });

      expect(fetchImpl).toHaveBeenCalledTimes(2);
      expect(verdict.pass).toBe(true);
    });

    it('retries once on network-level failure', async () => {
      const networkError = new Error('connection reset');
      const fetchImpl = jest
        .fn()
        .mockRejectedValueOnce(networkError)
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            choices: [{ message: { content: '{"pass": true, "reason": "ok"}' } }],
          }),
        });

      const verdict = await requestVisionVerdict({
        imagePng: Buffer.from('fake-png'),
        claim: 'visible',
        apiKey: 'test-key',
        fetchImpl: fetchImpl as unknown as typeof fetch,
      });

      expect(fetchImpl).toHaveBeenCalledTimes(2);
      expect(verdict.pass).toBe(true);
    });
  });
});
