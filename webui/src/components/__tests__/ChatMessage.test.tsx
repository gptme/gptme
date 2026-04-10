import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ChatMessage } from '../ChatMessage';
import '@testing-library/jest-dom';
import type { Message } from '@/types/conversation';
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
});
