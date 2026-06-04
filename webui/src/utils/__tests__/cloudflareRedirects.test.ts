import fs from 'fs';
import path from 'path';

describe('Cloudflare Pages redirect rules', () => {
  it('keeps API paths out of the SPA fallback', () => {
    const redirectsPath = path.resolve(__dirname, '../../../public/_redirects');
    const redirects = fs.readFileSync(redirectsPath, 'utf8');
    const rules = redirects
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#'));
    const expectedSpaRules = [
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

    expect(rules).toEqual(expect.arrayContaining(expectedSpaRules));
    expect(rules.some((rule) => rule.startsWith('/* '))).toBe(false);
    expect(rules.some((rule) => rule.startsWith('/api'))).toBe(false);
  });

  it('ships a dedicated 404 page for non-SPA misses', () => {
    const notFoundPath = path.resolve(__dirname, '../../../public/404.html');
    const notFoundPage = fs.readFileSync(notFoundPath, 'utf8');

    expect(notFoundPage).toContain('<h1>404 Not Found</h1>');
  });

  it('declares baseline security headers on the SPA shell', () => {
    const headersPath = path.resolve(__dirname, '../../../public/_headers');
    const content = fs.readFileSync(headersPath, 'utf8');

    // Parse _headers into sections: non-indented lines are path rules (section
    // headers); indented lines are header values scoped to the preceding rule.
    const sections: Record<string, string[]> = {};
    let currentSection = '';
    for (const line of content.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      if (!/^\s/.test(line)) {
        currentSection = trimmed;
        sections[currentSection] = [];
      } else if (currentSection) {
        sections[currentSection].push(trimmed);
      }
    }

    // Security headers must live under the `/*` wildcard rule so the SPA shell
    // is actually protected. A header that only appears under `/assets/*` or
    // any other more-specific rule would leave the shell unprotected.
    const spaHeaders = sections['/*'] ?? [];
    expect(spaHeaders).toEqual(
      expect.arrayContaining([
        'X-Frame-Options: SAMEORIGIN',
        'Strict-Transport-Security: max-age=31536000',
        'X-Content-Type-Options: nosniff',
        'Referrer-Policy: strict-origin-when-cross-origin',
      ])
    );
  });
});
