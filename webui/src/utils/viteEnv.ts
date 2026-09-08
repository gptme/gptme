/**
 * Vite-only env accessors isolated from Jest-imported modules.
 *
 * Jest's ts-jest CJS transform cannot parse `import.meta`, so any file that
 * Jest loads (notably `connectionConfig.ts`) must not mention it. Keep the
 * static `import.meta.env.DEV` member expression here — Vite replaces it with
 * a boolean literal. Do not wrap it in `Function('return import.meta.env.DEV')()`:
 * that string is a classic-script eval Vite cannot transform, so the access
 * always threw and Playwright e2e against `vite dev` never saw demo fixtures
 * (gptme/gptme#3754).
 */
export const isViteDev: boolean = import.meta.env.DEV;
