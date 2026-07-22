import '@testing-library/jest-dom';
import userEvent from '@testing-library/user-event';
import { render, screen } from '@testing-library/react';
import { ExternalSessionDetail } from '../ExternalSessionDetail';

const mockUseQuery = jest.fn();
const mockMutate = jest.fn();
const mockUseMutation = jest.fn();

jest.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => mockUseQuery(...args),
  useMutation: (...args: unknown[]) => mockUseMutation(...args),
}));

jest.mock('@legendapp/state/react', () => ({
  use$: jest.fn(() => true),
}));

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      isConnected$: {},
      getExternalSession: jest.fn(),
      steerExternalSession: jest.fn(),
    },
  }),
}));

const baseTranscript: { messages: { role: string; content: string }[]; capabilities: string[] } = {
  messages: [
    { role: 'user', content: 'do the thing' },
    { role: 'assistant', content: 'doing it' },
  ],
  capabilities: [],
};

function setupQueryAndMutation(
  transcriptOverrides: Partial<typeof baseTranscript> = {},
  mutationOverrides: object = {}
) {
  mockUseQuery.mockReturnValue({
    data: { transcript: { ...baseTranscript, ...transcriptOverrides } },
    isLoading: false,
    error: null,
  });
  mockUseMutation.mockReturnValue({
    mutate: mockMutate,
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
    ...mutationOverrides,
  });
}

describe('ExternalSessionDetail', () => {
  beforeEach(() => {
    mockMutate.mockReset();
  });

  it('renders transcript messages', () => {
    setupQueryAndMutation();
    render(<ExternalSessionDetail sessionId="abc123" />);
    expect(screen.getByText('do the thing')).toBeInTheDocument();
    expect(screen.getByText('doing it')).toBeInTheDocument();
  });

  it('does not show steer input when steer_inject is absent from capabilities', () => {
    setupQueryAndMutation({ capabilities: [] });
    render(<ExternalSessionDetail sessionId="abc123" />);
    expect(screen.queryByLabelText('Steer message')).not.toBeInTheDocument();
  });

  it('shows steer input when steer_inject is in capabilities', () => {
    setupQueryAndMutation({ capabilities: ['steer_inject'] });
    render(<ExternalSessionDetail sessionId="abc123" />);
    expect(screen.getByLabelText('Steer message')).toBeInTheDocument();
    expect(screen.getByLabelText('Send steer message')).toBeInTheDocument();
  });

  it('calls steer mutation when send button is clicked', async () => {
    setupQueryAndMutation({ capabilities: ['steer_inject'] });
    const user = userEvent.setup();
    render(<ExternalSessionDetail sessionId="abc123" />);
    const textarea = screen.getByLabelText('Steer message');
    await user.type(textarea, 'hello session');
    await user.click(screen.getByLabelText('Send steer message'));
    expect(mockMutate).toHaveBeenCalledWith('hello session');
  });

  it('calls steer mutation on Enter key without shift', async () => {
    setupQueryAndMutation({ capabilities: ['steer_inject'] });
    const user = userEvent.setup();
    render(<ExternalSessionDetail sessionId="abc123" />);
    const textarea = screen.getByLabelText('Steer message');
    await user.type(textarea, 'shortcut send');
    await user.keyboard('{Enter}');
    expect(mockMutate).toHaveBeenCalledWith('shortcut send');
  });

  it('disables send button when message is empty', () => {
    setupQueryAndMutation({ capabilities: ['steer_inject'] });
    render(<ExternalSessionDetail sessionId="abc123" />);
    expect(screen.getByLabelText('Send steer message')).toBeDisabled();
  });

  it('disables send button while mutation is pending', () => {
    setupQueryAndMutation({ capabilities: ['steer_inject'] }, { isPending: true });
    render(<ExternalSessionDetail sessionId="abc123" />);
    expect(screen.getByLabelText('Send steer message')).toBeDisabled();
  });

  it('shows error message when steer mutation fails', () => {
    setupQueryAndMutation(
      { capabilities: ['steer_inject'] },
      { isError: true, error: new Error('steer failed') }
    );
    render(<ExternalSessionDetail sessionId="abc123" />);
    expect(screen.getByText('steer failed')).toBeInTheDocument();
  });

  it('shows success message after steer', () => {
    setupQueryAndMutation({ capabilities: ['steer_inject'] }, { isSuccess: true });
    render(<ExternalSessionDetail sessionId="abc123" />);
    expect(screen.getByText('Message injected.')).toBeInTheDocument();
  });

  it('shows loading state while fetching', () => {
    mockUseQuery.mockReturnValue({ data: undefined, isLoading: true, error: null });
    mockUseMutation.mockReturnValue({
      mutate: mockMutate,
      isPending: false,
      isError: false,
      isSuccess: false,
      error: null,
    });
    render(<ExternalSessionDetail sessionId="abc123" />);
    expect(screen.getByText('Loading session…')).toBeInTheDocument();
  });

  it('shows error state when fetch fails', () => {
    mockUseQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('not found'),
    });
    mockUseMutation.mockReturnValue({
      mutate: mockMutate,
      isPending: false,
      isError: false,
      isSuccess: false,
      error: null,
    });
    render(<ExternalSessionDetail sessionId="abc123" />);
    expect(screen.getByText('Failed to load session details')).toBeInTheDocument();
  });
});
