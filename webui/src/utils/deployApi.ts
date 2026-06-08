import type { IApiClient } from '@/utils/api';
import { withLocalAddressSpace } from '@/utils/addressSpace';

export interface DeployStatus {
  enabled: boolean;
  configured: boolean;
  repository: string;
  workflow: string;
  ref: string;
  has_token: boolean;
  actions_url: string;
}

export interface DeployTriggerResponse {
  status: 'queued';
  message: string;
  repository: string;
  workflow: string;
  ref: string;
  actions_url: string;
}

interface DeployErrorResponse {
  error?: string;
  detail?: string;
  github_status?: number;
}

const DEPLOY_PATH = '/api/v2/dev/deploy-staging';

function deployEndpoint(api: IApiClient): string {
  return `${api.baseUrl.replace(/\/+$/, '')}${DEPLOY_PATH}`;
}

function deployHeaders(api: IApiClient): HeadersInit {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };

  if (api.authHeader) {
    headers.Authorization = api.authHeader;
  }

  return headers;
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const text = await response.text();

  if (!text) {
    return {} as T;
  }

  try {
    return JSON.parse(text) as T;
  } catch {
    return { error: text } as T;
  }
}

function formatDeployError(response: Response, payload: DeployErrorResponse): string {
  const parts = [payload.error || `Deploy request failed (${response.status})`];

  if (payload.github_status) {
    parts.push(`GitHub status ${payload.github_status}`);
  }

  if (payload.detail) {
    parts.push(payload.detail);
  }

  return parts.join(': ');
}

export async function getStagingDeployStatus(api: IApiClient): Promise<DeployStatus> {
  const url = deployEndpoint(api);
  const response = await fetch(
    url,
    withLocalAddressSpace(url, {
      headers: deployHeaders(api),
    })
  );
  const payload = await parseJsonResponse<DeployStatus & DeployErrorResponse>(response);

  if (!response.ok) {
    throw new Error(formatDeployError(response, payload));
  }

  return payload;
}

export async function triggerStagingDeploy(api: IApiClient): Promise<DeployTriggerResponse> {
  const url = deployEndpoint(api);
  const response = await fetch(
    url,
    withLocalAddressSpace(url, {
      method: 'POST',
      headers: deployHeaders(api),
      body: JSON.stringify({ source: 'gptme-webui-settings' }),
    })
  );
  const payload = await parseJsonResponse<DeployTriggerResponse & DeployErrorResponse>(response);

  if (!response.ok) {
    throw new Error(formatDeployError(response, payload));
  }

  return payload;
}
