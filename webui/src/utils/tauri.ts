// Detect if we're running in Tauri environment
export const isTauriEnvironment = () => {
  return typeof window !== 'undefined' && window.__TAURI__ !== undefined;
};

// Other Tauri-related utilities can be added here in the future
