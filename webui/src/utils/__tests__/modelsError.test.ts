import { buildModelsFetchError } from '../modelsError';

interface MockResponseOpts {
  status: number;
  statusText?: string;
  jsonBody?: unknown;
  jsonThrows?: boolean;
}

// Minimal stand-in for the parts of `Response` that buildModelsFetchError uses
// (jsdom does not provide a usable global Response).
function mockResponse(opts: MockResponseOpts): Response {
  return {
    status: opts.status,
    statusText: opts.statusText ?? '',
    clone() {
      return {
        json: async () => {
          if (opts.jsonThrows) {
            throw new Error('not json');
          }
          return opts.jsonBody;
        },
      };
    },
  } as unknown as Response;
}

describe('buildModelsFetchError', () => {
  it('surfaces the status code and server error body for a 401 with empty statusText', async () => {
    const err = await buildModelsFetchError(
      mockResponse({
        status: 401,
        statusText: '',
        jsonBody: { error: 'Missing authentication credentials' },
      })
    );
    expect(err.message).toBe('Failed to fetch models: 401 Missing authentication credentials');
  });

  it('falls back to statusText when the body has no error field', async () => {
    const err = await buildModelsFetchError(
      mockResponse({ status: 500, statusText: 'Internal Server Error', jsonBody: {} })
    );
    expect(err.message).toBe('Failed to fetch models: 500 Internal Server Error');
  });

  it('falls back to statusText when the body is not JSON', async () => {
    const err = await buildModelsFetchError(
      mockResponse({ status: 502, statusText: 'Bad Gateway', jsonThrows: true })
    );
    expect(err.message).toBe('Failed to fetch models: 502 Bad Gateway');
  });

  it('still reports the status code when neither body error nor statusText is present', async () => {
    const err = await buildModelsFetchError(
      mockResponse({ status: 401, statusText: '', jsonThrows: true })
    );
    expect(err.message).toBe('Failed to fetch models: 401');
  });
});
