// Script to be injected into the iframe to capture console logs
export const consoleProxyScript = `
  (function() {
    const originalConsole = {
      log: console.log,
      info: console.info,
      warn: console.warn,
      error: console.error,
      debug: console.debug
    };

    function proxyConsole(type) {
      return function(...args) {
        // Call original console method
        originalConsole[type].apply(console, args);

        // Forward to parent
        try {
          window.parent.postMessage({
            type: 'console',
            level: type,
            args: args.map(arg => {
              try {
                // Handle special cases like Error objects
                if (arg instanceof Error) {
                  return {
                    message: arg.message,
                    stack: arg.stack,
                    type: 'Error'
                  };
                }
                // Try to serialize the argument
                return JSON.parse(JSON.stringify(arg));
              } catch (e) {
                return String(arg);
              }
            })
          }, '*');
        } catch (e) {
          originalConsole.error('Failed to forward console message:', e);
        }
      };
    }

    // Override console methods
    console.log = proxyConsole('log');
    console.info = proxyConsole('info');
    console.warn = proxyConsole('warn');
    console.error = proxyConsole('error');
    console.debug = proxyConsole('debug');
  })();
`;

/**
 * Inject the console proxy into an iframe window.
 * Same-origin windows get the script. Cross-origin access throws SecurityError
 * and is skipped; any other failure is rethrown so same-origin bugs stay visible.
 */
export function injectConsoleProxy(contentWindow: Window): boolean {
  try {
    const script = new Function(consoleProxyScript);
    contentWindow.document.head.appendChild(
      Object.assign(contentWindow.document.createElement('script'), {
        textContent: `(${script.toString()})();`,
      })
    );
    return true;
  } catch (err) {
    if (err instanceof DOMException && err.name === 'SecurityError') {
      return false;
    }
    throw err;
  }
}
