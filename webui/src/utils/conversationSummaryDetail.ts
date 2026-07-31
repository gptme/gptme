export function shouldFetchDetailedConversationSummaries(env: {
  VITE_EMBEDDED_MODE?: string;
}): boolean {
  return env.VITE_EMBEDDED_MODE === 'true';
}
