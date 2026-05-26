import { describe, expect, it } from '@jest/globals';
import { readFileSync } from 'node:fs';
import path from 'node:path';

describe('Cloudflare Pages redirect config', () => {
  it('covers hosted SPA deep links for chat.gptme.org', () => {
    const redirects = readFileSync(path.resolve(process.cwd(), 'public/_redirects'), 'utf8');

    const expectedRules = [
      '/chat / 200',
      '/chat/* / 200',
      '/tasks / 200',
      '/tasks/* / 200',
      '/agents / 200',
      '/workspaces / 200',
      '/history / 200',
      '/external-sessions / 200',
      '/workspace/* / 200',
    ];

    for (const rule of expectedRules) {
      expect(redirects).toContain(rule);
    }
  });
});
