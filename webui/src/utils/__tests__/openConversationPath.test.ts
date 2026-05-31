import { buildFileUri, isLocalApiBaseUrl } from '../openConversationPath';

describe('isLocalApiBaseUrl', () => {
  it.each([
    ['http://localhost:5700'],
    ['http://127.0.0.1:5700'],
    ['http://[::1]:5700'],
    ['http://bob.localhost:5700'],
    ['/api/v2'],
  ])('allows local API URL %s', (baseUrl) => {
    expect(isLocalApiBaseUrl(baseUrl, 'http://localhost:3000')).toBe(true);
  });

  it.each([['https://gptme.ai'], ['https://example.com/api'], ['http://192.168.1.144:5700']])(
    'rejects remote API URL %s',
    (baseUrl) => {
      expect(isLocalApiBaseUrl(baseUrl, 'https://gptme.ai')).toBe(false);
    }
  );
});

describe('buildFileUri', () => {
  it('builds a file URI for POSIX paths', () => {
    expect(buildFileUri('/home/bob/gptme logs/chat #1')).toBe(
      'file:///home/bob/gptme%20logs/chat%20%231'
    );
  });

  it('builds a file URI for Windows paths', () => {
    expect(buildFileUri('C:\\Users\\Bob\\gptme logs\\chat #1')).toBe(
      'file:///C:/Users/Bob/gptme%20logs/chat%20%231'
    );
  });
});
