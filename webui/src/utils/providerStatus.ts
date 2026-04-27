export async function fetchProviderConfigured(
  baseUrl: string,
  authHeader: string | null,
  signal?: AbortSignal
): Promise<boolean> {
  const headers: Record<string, string> = authHeader ? { Authorization: authHeader } : {};
  const response = await fetch(`${baseUrl}/api/v2`, { headers, signal });
  if (!response.ok) {
    throw new Error(`Failed to verify provider status (${response.status})`);
  }
  const data = (await response.json()) as { provider_configured?: boolean };
  return data.provider_configured !== false;
}
