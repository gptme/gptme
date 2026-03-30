import { findLatestAssistantIndexForError } from '../conversationErrorHandling';

describe('findLatestAssistantIndexForError', () => {
  it('returns the last message when it is an assistant message', () => {
    expect(findLatestAssistantIndexForError([{ role: 'user' }, { role: 'assistant' }])).toBe(1);
  });

  it('returns the second-to-last assistant when a system error was appended after it', () => {
    expect(
      findLatestAssistantIndexForError([
        { role: 'user' },
        { role: 'assistant' },
        { role: 'system' },
      ])
    ).toBe(1);
  });

  it('returns -1 when there is no assistant message to clean up', () => {
    expect(findLatestAssistantIndexForError([{ role: 'user' }, { role: 'system' }])).toBe(-1);
  });
});
