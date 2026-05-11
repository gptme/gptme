import { getStepStartErrorMessage, toastStepStartError } from '../stepErrorHandling';

describe('getStepStartErrorMessage', () => {
  it('prefers the Error message when present', () => {
    expect(getStepStartErrorMessage(new Error('rate limit exceeded'))).toBe('rate limit exceeded');
  });

  it('accepts non-empty string errors', () => {
    expect(getStepStartErrorMessage('fleet API key exhausted')).toBe('fleet API key exhausted');
  });

  it('falls back when the error is empty or unknown', () => {
    expect(getStepStartErrorMessage(new Error(''))).toBe('Failed to start generation');
    expect(getStepStartErrorMessage(undefined)).toBe('Failed to start generation');
  });
});

describe('toastStepStartError', () => {
  it('emits a destructive generation toast', () => {
    const toast = jest.fn();

    toastStepStartError(toast, new Error('rate limit exceeded'));

    expect(toast).toHaveBeenCalledWith({
      variant: 'destructive',
      title: 'Generation failed',
      description: 'rate limit exceeded',
    });
  });
});
