declare global {
  interface Window {
    __TAURI__?: {
      app: unknown;
      event: unknown;
      invoke: unknown;
      core?: {
        invoke: unknown;
        [key: string]: unknown;
      };
      [key: string]: unknown;
    };
    __TAURI_INTERNALS__?: {
      invoke?: unknown;
      [key: string]: unknown;
    };
  }
}

export {};
