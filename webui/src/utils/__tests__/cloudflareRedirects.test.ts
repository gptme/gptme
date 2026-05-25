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
    const apiRuleIndex = rules.indexOf('/api/* /404.html 404');
    const spaFallbackIndex = rules.indexOf('/* /index.html 200');

    expect(apiRuleIndex).toBeGreaterThanOrEqual(0);
    expect(spaFallbackIndex).toBeGreaterThanOrEqual(0);
    expect(apiRuleIndex).toBeLessThan(spaFallbackIndex);
  });

  it('ships a dedicated 404 page for non-SPA misses', () => {
    const notFoundPath = path.resolve(__dirname, '../../../public/404.html');
    const notFoundPage = fs.readFileSync(notFoundPath, 'utf8');

    expect(notFoundPage).toContain('<h1>404 Not Found</h1>');
  });
});
