// Detect if we're running in Tauri environment.
//
// Tauri v2 only exposes `window.__TAURI__` when `app.withGlobalTauri` is enabled
// in tauri.conf.json (default: false). However, `window.__TAURI_INTERNALS__` is
// always injected by the Tauri runtime, so check it too — otherwise the webui
// silently takes the non-Tauri/manual onboarding path inside the packaged desktop
// app. See gptme/gptme#2226.
export const isTauriEnvironment = () => {
  if (typeof window === 'undefined') return false;
  return window.__TAURI__ !== undefined || window.__TAURI_INTERNALS__ !== undefined;
};

// Invoke a Tauri IPC command. Tauri v2 exposes `invoke` on `window.__TAURI__.core`
// when `withGlobalTauri` is true, and on `window.__TAURI_INTERNALS__` regardless.
// Keeping this as a thin wrapper avoids adding `@tauri-apps/api` as a webui
// dependency just to call a handful of commands from the desktop shell.
export async function invokeTauri<T = unknown>(
  command: string,
  args?: Record<string, unknown>
): Promise<T> {
  if (typeof window === 'undefined') {
    throw new Error('Tauri API is not available in this environment');
  }
  const globalCore = (window.__TAURI__ as { core?: { invoke?: unknown } } | undefined)?.core;
  const invoke = globalCore?.invoke ?? window.__TAURI_INTERNALS__?.invoke;
  if (typeof invoke !== 'function') {
    throw new Error('Tauri invoke API is not available');
  }
  return (invoke as (cmd: string, args?: Record<string, unknown>) => Promise<T>)(command, args);
}
