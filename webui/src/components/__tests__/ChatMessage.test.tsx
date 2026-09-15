import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ChatMessage, isSystemErrorContent, isSystemSuccessContent } from '../ChatMessage';
import { MessageAvatar } from '../MessageAvatar';
import '@testing-library/jest-dom';
import type { Message, MessageRole } from '@/types/conversation';
import { observable } from '@legendapp/state';
import { SettingsProvider } from '@/contexts/SettingsContext';

// Mock the ApiContext
jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    baseUrl: 'http://localhost:5700',
    connectionConfig: {
      apiKey: '',
      baseUrl: 'http://localhost:5700',
    },
    api: {
      userInfo$: observable(null),
    },
  }),
}));

describe('ChatMessage', () => {
  const testConversationId = 'test-conversation';

  // Helper function to render with providers
  const renderWithProviders = (component: React.ReactElement) => {
    return render(<SettingsProvider>{component}</SettingsProvider>);
  };

  it('renders user message', () => {
    const message$ = observable<Message>({
      role: 'user',
      content: 'Hello!',
      timestamp: new Date().toISOString(),
    });

    renderWithProviders(<ChatMessage message$={message$} conversationId={testConversationId} />);
    expect(screen.getByText('Hello!')).toBeInTheDocument();
  });

  it('renders a compact visible user avatar fallback', () => {
    const message$ = observable<Message>({
      role: 'user',
      content: 'Hello!',
      timestamp: new Date().toISOString(),
    });

    renderWithProviders(<ChatMessage message$={message$} conversationId={testConversationId} />);

    const avatar = screen.getByLabelText('User avatar');
    expect(avatar).toBeInTheDocument();
    expect(avatar).toHaveTextContent('U');
    expect(avatar).not.toHaveClass('hidden');
  });

  it('uses initials for named user avatar fallback', () => {
    render(
      <MessageAvatar
        role$={observable<MessageRole>('user')}
        chainType$={observable<'start' | 'middle' | 'end' | 'standalone'>('standalone')}
        userName="Erik Bjareholt"
      />
    );

    const avatar = screen.getByLabelText('Erik Bjareholt avatar');
    expect(avatar).toHaveTextContent('EB');
    expect(avatar).not.toHaveClass('hidden');
  });

  it('renders assistant message', () => {
    const message$ = observable<Message>({
      role: 'assistant',
      content: 'Hi there!',
      timestamp: new Date().toISOString(),
    });

    renderWithProviders(<ChatMessage message$={message$} conversationId={testConversationId} />);
    expect(screen.getByText('Hi there!')).toBeInTheDocument();
  });

  it('renders system message with monospace font', () => {
    const message$ = observable<Message>({
      role: 'system',
      content: 'System message',
      timestamp: new Date().toISOString(),
    });

    const { container } = renderWithProviders(
      <ChatMessage message$={message$} conversationId={testConversationId} />
    );
    const messageElement = container.querySelector('.font-mono');
    expect(messageElement).toBeInTheDocument();
  });

  it('renders copy button on messages', () => {
    const message$ = observable<Message>({
      role: 'assistant',
      content: 'Some response text',
      timestamp: new Date().toISOString(),
    });

    renderWithProviders(<ChatMessage message$={message$} conversationId={testConversationId} />);
    const copyButton = screen.getByRole('button', { name: 'Copy message' });
    expect(copyButton).toBeInTheDocument();
  });

  it('renders fork button and calls handler with the message index', () => {
    const onFork = jest.fn();
    const message$ = observable<Message>({
      role: 'assistant',
      content: 'Some response text',
      timestamp: new Date().toISOString(),
    });

    renderWithProviders(
      <ChatMessage
        message$={message$}
        conversationId={testConversationId}
        messageIndex={3}
        onFork={onFork}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Fork from here' }));
    expect(onFork).toHaveBeenCalledWith(3);
  });

  it('copies message content to clipboard on copy button click', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: { writeText },
    });

    const message$ = observable<Message>({
      role: 'assistant',
      content: 'Text to copy',
      timestamp: new Date().toISOString(),
    });

    renderWithProviders(<ChatMessage message$={message$} conversationId={testConversationId} />);
    const copyButton = screen.getByRole('button', { name: 'Copy message' });
    fireEvent.click(copyButton);
    expect(writeText).toHaveBeenCalledWith('Text to copy');
  });

  it('shows check icon after successful copy', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: { writeText },
    });

    const message$ = observable<Message>({
      role: 'user',
      content: 'My message',
      timestamp: new Date().toISOString(),
    });

    renderWithProviders(<ChatMessage message$={message$} conversationId={testConversationId} />);

    const copyButton = screen.getByRole('button', { name: 'Copy message' });
    expect(copyButton).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(copyButton);
    });

    expect(writeText).toHaveBeenCalledWith('My message');

    await waitFor(() => {
      expect(copyButton.querySelector('svg.lucide-check')).toBeInTheDocument();
      expect(copyButton.querySelector('svg.lucide-clipboard')).not.toBeInTheDocument();
    });
  });

  it('does not show success icon when clipboard write fails', async () => {
    const writeText = jest.fn().mockRejectedValue(new Error('Permission denied'));
    Object.assign(navigator, {
      clipboard: { writeText },
    });
    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

    const message$ = observable<Message>({
      role: 'assistant',
      content: 'Some text',
      timestamp: new Date().toISOString(),
    });

    renderWithProviders(<ChatMessage message$={message$} conversationId={testConversationId} />);

    const copyButton = screen.getByRole('button', { name: 'Copy message' });
    await act(async () => {
      fireEvent.click(copyButton);
    });

    await waitFor(() => {
      expect(consoleSpy).toHaveBeenCalledWith('Failed to copy to clipboard:', expect.any(Error));
    });

    expect(copyButton.querySelector('svg.lucide-clipboard')).toBeInTheDocument();
    expect(copyButton.querySelector('svg.lucide-check')).not.toBeInTheDocument();

    consoleSpy.mockRestore();
  });

  it('renders system message with empty content without throwing', () => {
    const message$ = observable<Message>({
      role: 'system',
      content: '',
      timestamp: new Date().toISOString(),
    });

    expect(() =>
      renderWithProviders(<ChatMessage message$={message$} conversationId={testConversationId} />)
    ).not.toThrow();
  });

  it('renders system message when content is undefined without throwing', () => {
    const message$ = observable({
      role: 'system',
      content: undefined,
      timestamp: new Date().toISOString(),
    } as unknown as Message);

    expect(() =>
      renderWithProviders(<ChatMessage message$={message$} conversationId={testConversationId} />)
    ).not.toThrow();
  });
});

describe('isSystemErrorContent', () => {
  it('returns false for undefined without throwing', () => {
    expect(() => isSystemErrorContent(undefined)).not.toThrow();
    expect(isSystemErrorContent(undefined)).toBe(false);
  });

  it('returns false for empty string', () => {
    expect(isSystemErrorContent('')).toBe(false);
  });

  it('returns true when content starts with Error', () => {
    expect(isSystemErrorContent('Error: file not found')).toBe(true);
  });

  it('returns false for non-error content', () => {
    expect(isSystemErrorContent('Saved foo.py')).toBe(false);
  });
});

describe('isSystemSuccessContent', () => {
  it('returns false for undefined without throwing', () => {
    expect(() => isSystemSuccessContent(undefined)).not.toThrow();
    expect(isSystemSuccessContent(undefined)).toBe(false);
  });

  it('returns false for empty string', () => {
    expect(isSystemSuccessContent('')).toBe(false);
  });

  it('returns true for saved/appended/success prefixes', () => {
    expect(isSystemSuccessContent('Saved foo.py')).toBe(true);
    expect(isSystemSuccessContent('Appended to foo.py')).toBe(true);
    expect(isSystemSuccessContent('Patch applied successfully')).toBe(true);
  });

  it('returns false for error content', () => {
    expect(isSystemSuccessContent('Error: file not found')).toBe(false);
  });
});
