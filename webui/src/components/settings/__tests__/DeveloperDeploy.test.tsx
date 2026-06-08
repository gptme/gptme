import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import { DeveloperDeploy } from '../DeveloperDeploy';

const mockGetPrimaryClient = jest.fn();
const mockGetStagingDeployStatus = jest.fn();
const mockTriggerStagingDeploy = jest.fn();

jest.mock('@/stores/serverClients', () => ({
  getPrimaryClient: () => mockGetPrimaryClient(),
}));

jest.mock('@/utils/deployApi', () => ({
  getStagingDeployStatus: (...args: unknown[]) => mockGetStagingDeployStatus(...args),
  triggerStagingDeploy: (...args: unknown[]) => mockTriggerStagingDeploy(...args),
}));

jest.mock('sonner', () => ({
  toast: {
    success: jest.fn(),
    error: jest.fn(),
  },
}));

describe('DeveloperDeploy', () => {
  beforeEach(() => {
    mockGetPrimaryClient.mockReset();
    mockGetStagingDeployStatus.mockReset();
    mockTriggerStagingDeploy.mockReset();
    mockGetPrimaryClient.mockReturnValue({ baseUrl: 'http://127.0.0.1:5700' });
  });

  it('disables deploy when the server reports the trigger is not configured', async () => {
    mockGetStagingDeployStatus.mockResolvedValue({
      enabled: true,
      configured: false,
      repository: 'gptme/gptme',
      workflow: 'webui-staging.yml',
      ref: 'master',
      has_token: false,
      actions_url: 'https://github.com/gptme/gptme/actions/workflows/webui-staging.yml',
    });

    render(<DeveloperDeploy />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Deploy' })).toBeDisabled();
    });
  });
});
