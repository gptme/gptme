import { renderHook } from '@testing-library/react';
import { useWorkspaceApi } from '../workspaceApi';

const mockApi = { baseUrl: 'https://server.invalid/prefix', authHeader: 'Bearer test-token' };
jest.mock('@/contexts/ApiContext', () => ({ useApi: () => ({ api: mockApi }) }));

const originalFetch = global.fetch;
const mockFetch = jest.fn();
beforeEach(() => {
  mockFetch.mockReset();
  global.fetch = mockFetch;
  mockFetch.mockResolvedValue({
    ok: true,
    headers: new Headers({ 'Content-Type': 'model/gltf+json' }),
  });
});
afterEach(() => {
  global.fetch = originalFetch;
});

it.each(['workspace', 'attachments'] as const)(
  'keeps %s in the model and relative resource URLs',
  async (root) => {
    const { result } = renderHook(() => useWorkspaceApi());
    const preview = await result.current.previewFile('conversation', 'scene/model one.gltf', root);
    expect(mockFetch).toHaveBeenCalledWith(
      `https://server.invalid/prefix/api/v2/conversations/conversation/workspace/scene/model%20one.gltf/preview${root === 'attachments' ? '?root=attachments' : ''}`,
      { headers: { Authorization: 'Bearer test-token' }, signal: undefined }
    );
    expect(preview.type).toBe('model3d');
    if (preview.type !== 'model3d') throw new Error('Expected model preview');
    const base = `https://server.invalid/prefix/api/v2/conversations/conversation/${root}/`;
    expect(preview.content).toBe(`${base}scene/model%20one.gltf`);
    expect(new URL('mesh.bin', preview.content).href).toBe(`${base}scene/mesh.bin`);
    expect(new URL('textures/base%20color.png', preview.content).href).toBe(
      `${base}scene/textures/base%20color.png`
    );
    expect(new URL('../shared.bin', preview.content).href).toBe(`${base}shared.bin`);
  }
);

it('keeps self-contained attachment GLB previews on the authenticated blob path', async () => {
  const blob = new Blob(['model'], { type: 'model/gltf-binary' });
  mockFetch.mockResolvedValue({
    ok: true,
    headers: new Headers({ 'Content-Type': 'model/gltf-binary' }),
    blob: async () => blob,
  });
  const originalCreate = URL.createObjectURL;
  URL.createObjectURL = jest.fn(() => 'blob:model');
  try {
    const { result } = renderHook(() => useWorkspaceApi());
    await expect(
      result.current.previewFile('conversation', 'scene.glb', 'attachments')
    ).resolves.toEqual({
      type: 'model3d',
      content: 'blob:model',
      mime_type: 'model/gltf-binary',
    });
    expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
  } finally {
    URL.createObjectURL = originalCreate;
  }
});
