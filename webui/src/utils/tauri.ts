// Detect if we're running in Tauri environment
export const isTauriEnvironment = () => {
  return typeof window !== 'undefined' && window.__TAURI__ !== undefined;
};

export const isTauriMobileEnvironment = () => {
  return (
    isTauriEnvironment() &&
    typeof navigator !== 'undefined' &&
    /Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
  );
};

export const tauriManagesLocalServer = () => {
  return isTauriEnvironment() && !isTauriMobileEnvironment();
};
