declare global {
  interface Window {
    __TAURI__?: {
      app: unknown;
      event: unknown;
      invoke: unknown;
      [key: string]: unknown;
    };
  }
}

export {};
