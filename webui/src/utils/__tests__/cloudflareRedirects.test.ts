import fs from 'fs';
import path from 'path';
import { cloudflareSpaRedirectRules } from '@/appRoutes';

describe('Cloudflare Pages redirect rules', () => {
  it('covers every SPA deep-link route without catching API paths', () => {
    const redirectsPath = path.resolve(__dirname, '../../../public/_redirects');
    const redirects = fs.readFileSync(redirectsPath, 'utf8');
    const rules = redirects
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#'));

    expect(rules).toEqual(expect.arrayContaining(cloudflareSpaRedirectRules));
    expect(rules.some((rule) => rule.startsWith('/* '))).toBe(false);
    expect(rules.some((rule) => rule.startsWith('/api'))).toBe(false);
  });

  it('ships a dedicated 404 page for non-SPA misses', () => {
    const notFoundPath = path.resolve(__dirname, '../../../public/404.html');
    const notFoundPage = fs.readFileSync(notFoundPath, 'utf8');

    expect(notFoundPage).toContain('<h1>404 Not Found</h1>');
  });
});
