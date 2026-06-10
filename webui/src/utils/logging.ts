import { isTauriEnvironment } from './tauri';

let detachConsole: (() => void) | null = null;

// Simplified console forwarding following Tauri documentation pattern
function forwardConsole(
  fnName: 'log' | 'debug' | 'info' | 'warn' | 'error',
  logger: (message: string) => Promise<void>
) {
  const original = console[fnName];
  console[fnName] = (...args) => {
    // Call original console method
    original(...args);

    // Forward to Tauri logging
    const message = args
      .map((arg) =>
        typeof arg === 'string'
          ? arg
          : (() => {
              try {
                return JSON.stringify(arg);
              } catch {
                return '[Unserializable]';
              }
            })()
      )
      .join(' ');

    logger(message).catch((err) => original('Failed to forward to Tauri log:', err));
  };
}

export async function setupLogging() {
  // Only setup Tauri logging if we're in a Tauri environment
  if (!isTauriEnvironment()) {
    console.info('Running in browser mode - using console logging');
    return;
  }

  try {
    // Dynamically import Tauri logging to avoid module errors in browser
    const { warn, debug, trace, info, error, attachConsole } = await import(
      '@tauri-apps/plugin-log'
    );

    // Attach console first to see Rust logs in browser console
    detachConsole = await attachConsole();

    // Then forward browser console logs to Tauri logging system
    forwardConsole('log', trace);
    forwardConsole('debug', debug);
    forwardConsole('info', info);
    forwardConsole('warn', warn);
    forwardConsole('error', error);

    console.info('✅ Tauri logging setup complete');
    console.info('  • Rust logs → Browser console');
    console.info('  • Browser console → Tauri logs');

    // Test logging
    await info('Frontend logging initialized');
  } catch (err) {
    console.error('Failed to setup Tauri logging:', err);
  }
}

export function cleanupLogging() {
  if (detachConsole) {
    detachConsole();
    detachConsole = null;
  }
}

// Export logging functions for direct use
export const warn = async (message: string) => {
  if (isTauriEnvironment()) {
    try {
      const { warn } = await import('@tauri-apps/plugin-log');
      return warn(message);
    } catch {
      console.warn(message);
    }
  } else {
    console.warn(message);
  }
};

export const debug = async (message: string) => {
  if (isTauriEnvironment()) {
    try {
      const { debug } = await import('@tauri-apps/plugin-log');
      return debug(message);
    } catch {
      console.debug(message);
    }
  } else {
    console.debug(message);
  }
};

export const trace = async (message: string) => {
  if (isTauriEnvironment()) {
    try {
      const { trace } = await import('@tauri-apps/plugin-log');
      return trace(message);
    } catch {
      console.log(message);
    }
  } else {
    console.log(message);
  }
};

export const info = async (message: string) => {
  if (isTauriEnvironment()) {
    try {
      const { info } = await import('@tauri-apps/plugin-log');
      return info(message);
    } catch {
      console.info(message);
    }
  } else {
    console.info(message);
  }
};

export const error = async (message: string) => {
  if (isTauriEnvironment()) {
    try {
      const { error } = await import('@tauri-apps/plugin-log');
      return error(message);
    } catch {
      console.error(message);
    }
  } else {
    console.error(message);
  }
};
