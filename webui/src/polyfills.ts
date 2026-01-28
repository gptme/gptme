// Polyfill for crypto.randomUUID in development/unsecure contexts
// See: https://github.com/pyscript/pyscript/issues/2326
if (typeof crypto !== 'undefined' && !crypto.randomUUID) {
  crypto.randomUUID = function randomUUID(): `${string}-${string}-${string}-${string}-${string}` {
    // Simple UUID v4 generator
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    }) as `${string}-${string}-${string}-${string}-${string}`;
  };
}
