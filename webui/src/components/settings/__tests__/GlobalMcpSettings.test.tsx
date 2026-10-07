import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { GlobalMcpSettings } from '../GlobalMcpSettings';
import { toast } from 'sonner';

const mockApi = { baseUrl: 'http://localhost:5700', authHeader: 'Bearer test-token' };
jest.mock('@/contexts/ApiContext', () => ({ useApi: () => ({ api: mockApi }) }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
const mockFetch = jest.fn();
const config = {
  enabled: true,
  auto_start: false,
  path: '/tmp/config.toml',
  servers: [
    {
      name: 'remote',
      enabled: true,
      command: '',
      args: ['contains, comma', ' spaced '],
      env: { API_TOKEN: '***' },
      url: 'https://example.com/mcp',
      headers: { Authorization: '***' },
    },
  ],
};
const response = (data: unknown, ok = true) => ({ ok, json: async () => data });

beforeEach(() => {
  jest.clearAllMocks();
  mockFetch.mockReset();
  global.fetch = mockFetch;
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

it('loads the shared form and preserves HTTP fields, secrets and unchanged argument arrays on save', async () => {
  mockFetch.mockResolvedValueOnce(response(config)).mockResolvedValueOnce(response(config));
  render(<GlobalMcpSettings />);
  await screen.findByText('/tmp/config.toml');
  fireEvent.click(screen.getByRole('button', { name: /MCP Servers.*Add or remove/ }));
  expect(screen.getByLabelText('Server Name')).toHaveValue('remote');
  fireEvent.click(screen.getByRole('switch', { name: 'Auto-Start MCP Servers' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save global MCP settings' }));
  await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
  expect(mockFetch).toHaveBeenLastCalledWith(
    'http://localhost:5700/api/v2/user/config/mcp',
    expect.objectContaining({
      method: 'PUT',
      headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: true, auto_start: true, servers: config.servers }),
    })
  );
  await waitFor(() => expect(toast.success).toHaveBeenCalled());
});

it('cannot save an empty replacement after loading fails', async () => {
  mockFetch.mockResolvedValue(response({ error: 'Unavailable' }, false));
  render(<GlobalMcpSettings />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Unavailable');
  expect(
    screen.queryByRole('button', { name: 'Save global MCP settings' })
  ).not.toBeInTheDocument();
});

it('shows server validation errors and retains unsaved edits', async () => {
  mockFetch
    .mockResolvedValueOnce(response(config))
    .mockResolvedValueOnce(response({ error: 'duplicate server name' }, false));
  render(<GlobalMcpSettings />);
  await screen.findByText('/tmp/config.toml');
  fireEvent.click(screen.getByRole('switch', { name: 'Auto-Start MCP Servers' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save global MCP settings' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('duplicate server name');
  expect(screen.getByRole('switch', { name: 'Auto-Start MCP Servers' })).toBeChecked();
  expect(screen.getByRole('button', { name: 'Save global MCP settings' })).toBeEnabled();
});

it('adds servers through the reused form', async () => {
  const empty = { ...config, servers: [] };
  mockFetch.mockResolvedValue(response(empty));
  render(<GlobalMcpSettings />);
  await screen.findByText('/tmp/config.toml');
  fireEvent.click(screen.getByRole('button', { name: /MCP Servers.*Add or remove/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Add MCP Server' }));
  fireEvent.change(screen.getByLabelText('Server Name'), { target: { value: 'local' } });
  fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'python' } });
  fireEvent.change(screen.getByLabelText('Arguments (comma-separated)'), {
    target: { value: '-m, my_server' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save global MCP settings' }));
  await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
  expect(JSON.parse(mockFetch.mock.calls[1][1].body).servers).toEqual([
    {
      name: 'local',
      enabled: true,
      command: 'python',
      args: ['-m', 'my_server'],
      env: {},
      url: '',
      headers: {},
    },
  ]);
});
