import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ComputerPreview } from '../ComputerPreview';

// Mock useApi so we don't need the full ApiContext tree
jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: { authHeader: null },
    connectionConfig: { baseUrl: 'http://127.0.0.1:5700' },
  }),
}));

// jsdom doesn't implement URL.createObjectURL — stub it
beforeAll(() => {
  global.URL.createObjectURL = jest.fn(() => 'blob:mock-url');
  global.URL.revokeObjectURL = jest.fn();
});

function mockFetch(screenshotStatus: 200 | 503 = 200, backendAvailable = true) {
  const statusPayload = {
    screenshot_available: backendAvailable,
    system: 'Linux',
    display: ':1',
    backends: backendAvailable ? { xdotool: true, scrot: true } : {},
  };

  global.fetch = jest.fn().mockImplementation((url: string) => {
    if (url.includes('/status')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(statusPayload),
      });
    }
    if (screenshotStatus === 503) {
      return Promise.resolve({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ error: 'Screenshot backend unavailable' }),
      });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      blob: () => Promise.resolve(new Blob(['png'], { type: 'image/png' })),
    });
  });
}

describe('ComputerPreview', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows loading state on mount', () => {
    global.fetch = jest.fn().mockReturnValue(new Promise(() => {})); // never resolves
    render(<ComputerPreview />);
    expect(screen.getByText(/connecting to desktop/i)).toBeInTheDocument();
  });

  it('renders a screenshot image when the API returns 200', async () => {
    mockFetch(200);
    render(<ComputerPreview />);
    await waitFor(() => {
      expect(screen.getByRole('img', { name: /desktop screenshot/i })).toBeInTheDocument();
    });
  });

  it('shows error message when API returns 503', async () => {
    mockFetch(503, false);
    render(<ComputerPreview />);
    await waitFor(() => {
      expect(screen.getByText(/screenshot backend unavailable/i)).toBeInTheDocument();
    });
  });

  it('shows backend chips when status endpoint returns available backends', async () => {
    mockFetch(200, true);
    render(<ComputerPreview />);
    await waitFor(() => {
      expect(screen.getByText('xdotool')).toBeInTheDocument();
      expect(screen.getByText('scrot')).toBeInTheDocument();
    });
  });

  it('switches to VNC iframe when VNC button is clicked', async () => {
    const user = userEvent.setup();
    mockFetch(200);
    render(<ComputerPreview />);

    // The VNC button is always present in the toolbar
    const vncButton = screen.getByTitle(/vnc viewer/i);
    await user.click(vncButton);

    expect(screen.getByTitle('VNC Viewer')).toBeInTheDocument();
    expect(screen.getByTitle('VNC Viewer').tagName).toBe('IFRAME');
  });

  it('returns to screenshot view when back button is clicked in VNC mode', async () => {
    const user = userEvent.setup();
    mockFetch(200);
    render(<ComputerPreview />);

    await user.click(screen.getByTitle(/vnc viewer/i));
    expect(screen.getByTitle('VNC Viewer').tagName).toBe('IFRAME');

    await user.click(screen.getByTitle('Back to screenshot view'));
    await waitFor(() => {
      expect(screen.queryByTitle('VNC Viewer')).not.toBeInTheDocument();
    });
  });
});
