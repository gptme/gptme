// Detect if we're running in Tauri environment
export const isTauriEnvironment = () => {
  return typeof window !== 'undefined' && window.__TAURI__ !== undefined;
};

// Invoke a Tauri IPC command. Tauri v2 exposes `invoke` on `window.__TAURI__.core`
// (see https://v2.tauri.app/reference/javascript/api/namespacecore/). Keeping this
// as a thin wrapper avoids adding `@tauri-apps/api` as a webui dependency just to
// call a handful of commands from the desktop shell.
export async function invokeTauri<T = unknown>(
  command: string,
  args?: Record<string, unknown>
): Promise<T> {
  if (typeof window === 'undefined' || window.__TAURI__ === undefined) {
    throw new Error('Tauri API is not available in this environment');
  }
  const core = (window.__TAURI__ as { core?: { invoke?: unknown } }).core;
  const invoke = core?.invoke;
  if (typeof invoke !== 'function') {
    throw new Error('Tauri invoke API is not available');
  }
  return (invoke as (cmd: string, args?: Record<string, unknown>) => Promise<T>)(command, args);
}
