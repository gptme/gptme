import { shouldFetchDetailedConversationSummaries } from '../conversationSummaryDetail';

describe('shouldFetchDetailedConversationSummaries', () => {
  it('enables detailed summaries in embedded mode', () => {
    expect(shouldFetchDetailedConversationSummaries({ VITE_EMBEDDED_MODE: 'true' })).toBe(true);
  });

  it('keeps the default webui list query cheap outside embedded mode', () => {
    expect(shouldFetchDetailedConversationSummaries({})).toBe(false);
    expect(shouldFetchDetailedConversationSummaries({ VITE_EMBEDDED_MODE: 'false' })).toBe(false);
  });
});
