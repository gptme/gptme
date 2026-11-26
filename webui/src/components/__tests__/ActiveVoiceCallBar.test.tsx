import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ActiveVoiceCallBar } from '../ActiveVoiceCallBar';

const mockStop = jest.fn();
let mockCall: { state: string; conversationId: string | null; serverId: string | null } | null =
  null;
jest.mock('@/contexts/VoiceCallContext', () => ({
  useOptionalVoiceCall: () => (mockCall ? { ...mockCall, stop: mockStop } : null),
}));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ActiveVoiceCallBar />
    </MemoryRouter>
  );
}

describe('ActiveVoiceCallBar', () => {
  beforeEach(() => {
    mockStop.mockReset();
    mockCall = { state: 'recording', conversationId: 'chat-1', serverId: null };
  });

  it('shows a live call on other pages and ends it', () => {
    renderAt('/settings');
    expect(screen.getByRole('status', { name: 'Voice call active' })).toBeInTheDocument();
    screen.getByRole('button', { name: 'End voice call' }).click();
    expect(mockStop).toHaveBeenCalled();
  });

  it("stays out of the way on the call's own chat", () => {
    renderAt('/chat/chat-1');
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('renders nothing without a live call', () => {
    mockCall = { state: 'idle', conversationId: 'chat-1', serverId: null };
    renderAt('/settings');
    expect(screen.queryByRole('status')).toBeNull();
  });
});
