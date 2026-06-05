type LegacyGenerationProgressEvent = {
  type: 'generation_progress';
  data?: { content?: string };
  token?: string;
};

type GenerationCompleteEvent = {
  type: 'generation_complete';
};

type ErrorEvent = {
  type: 'error';
  error?: string;
};

type StreamEvent = LegacyGenerationProgressEvent | GenerationCompleteEvent | ErrorEvent;

export type ParsedExtensionStreamEvent =
  | { type: 'token'; token: string }
  | { type: 'complete' }
  | { type: 'error'; error: string };

export function parseExtensionStreamLine(line: string): ParsedExtensionStreamEvent | null {
  if (!line.startsWith('data: ')) return null;

  try {
    const event = JSON.parse(line.slice(6)) as StreamEvent;

    if (event.type === 'generation_progress') {
      const token =
        typeof event.token === 'string'
          ? event.token
          : typeof event.data?.content === 'string'
            ? event.data.content
            : null;
      return token ? { type: 'token', token } : null;
    }

    if (event.type === 'generation_complete') {
      return { type: 'complete' };
    }

    if (event.type === 'error') {
      return {
        type: 'error',
        error: typeof event.error === 'string' ? event.error : 'SSE connection lost',
      };
    }

    return null;
  } catch {
    return null;
  }
}
