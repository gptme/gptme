import fs from 'fs';
import path from 'path';

describe('Cloudflare Pages redirect rules', () => {
  it('keeps API paths out of the SPA fallback', () => {
    const redirectsPath = path.resolve(process.cwd(), 'public/_redirects');
    const redirects = fs.readFileSync(redirectsPath, 'utf8');
    const rules = redirects
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#'));

    expect(rules).toEqual(['/api/* /404.html 404', '/* /index.html 200']);
  });

  it('ships a dedicated 404 page for non-SPA misses', () => {
    const notFoundPath = path.resolve(process.cwd(), 'public/404.html');
    const notFoundPage = fs.readFileSync(notFoundPath, 'utf8');

    expect(notFoundPage).toContain('<h1>404 Not Found</h1>');
  });
});
