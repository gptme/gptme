import { injectConsoleProxy } from '../consoleProxy';

function mockWindow(document: object): Window {
  return { document } as unknown as Window;
}

describe('injectConsoleProxy', () => {
  it('injects a script into a same-origin document', () => {
    const appendChild = jest.fn();
    const scriptEl = { textContent: '' };
    const createElement = jest.fn(() => scriptEl);
    const injected = injectConsoleProxy(
      mockWindow({
        head: { appendChild },
        createElement,
      })
    );

    expect(injected).toBe(true);
    expect(createElement).toHaveBeenCalledWith('script');
    expect(appendChild).toHaveBeenCalledWith(scriptEl);
    expect(scriptEl.textContent).toContain('function');
  });

  it('returns false on cross-origin SecurityError instead of throwing', () => {
    const win = mockWindow(
      new Proxy(
        {},
        {
          get() {
            throw new DOMException(
              'Blocked a frame with origin from accessing a cross-origin frame.',
              'SecurityError'
            );
          },
        }
      )
    );

    expect(injectConsoleProxy(win)).toBe(false);
  });

  it('rethrows non-SecurityError failures so same-origin bugs stay visible', () => {
    const win = mockWindow({
      get head() {
        throw new TypeError('Cannot read properties of null (reading "head")');
      },
      createElement: jest.fn(),
    });

    expect(() => injectConsoleProxy(win)).toThrow(TypeError);
  });
});
