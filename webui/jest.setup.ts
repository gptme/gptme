import '@testing-library/jest-dom';
import { TextDecoder, TextEncoder } from 'util';

// react-dom/server.browser expects browser text encoding globals. jsdom does not
// expose them in Jest, while real browsers and Node do.
if (typeof globalThis.TextEncoder === 'undefined') {
  globalThis.TextEncoder = TextEncoder;
}
if (typeof globalThis.TextDecoder === 'undefined') {
  globalThis.TextDecoder = TextDecoder as typeof globalThis.TextDecoder;
}

// Polyfill structuredClone for jsdom — Node 17+ has it but jsdom doesn't expose it.
// Limitation: JSON.parse/JSON.stringify drops undefined properties, coerces Date to
// string, throws on BigInt, and silently converts Map/Set to {}. Safe for the current
// JSON-compatible conversation objects; revisit if tests start using those types.
if (typeof structuredClone === 'undefined') {
  global.structuredClone = <T>(val: T): T => JSON.parse(JSON.stringify(val));
}

// Shim Vite import.meta.env for Jest.
// connectionConfig.ts and SetupWizard.tsx wrap import.meta.env accesses in
// Function() and fall back to process.env when that throws.
// Keep these in sync with the hardcoded defaults in connectionConfig.ts.
process.env.VITE_GPTME_CLOUD_BASE_URL = 'https://gptme.ai';
process.env.VITE_GPTME_FLEET_BASE_URL = 'https://fleet.gptme.ai';
process.env.VITE_GPTME_API_URL = 'http://127.0.0.1:5700';
