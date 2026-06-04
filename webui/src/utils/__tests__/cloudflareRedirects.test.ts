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
    const headers = fs.readFileSync(headersPath, 'utf8');
    const lines = headers
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#'));

    expect(lines).toEqual(
      expect.arrayContaining([
        'X-Frame-Options: SAMEORIGIN',
        'Strict-Transport-Security: max-age=31536000',
        'X-Content-Type-Options: nosniff',
        'Referrer-Policy: strict-origin-when-cross-origin',
      ])
    );
  });
});
