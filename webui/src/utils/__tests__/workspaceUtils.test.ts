import { isAgentWorkspace } from '../workspaceUtils';

describe('isAgentWorkspace', () => {
  it('returns false for undefined without throwing', () => {
    expect(() => isAgentWorkspace(undefined)).not.toThrow();
    expect(isAgentWorkspace(undefined)).toBe(false);
  });

  it('returns false for empty string without throwing', () => {
    expect(() => isAgentWorkspace('')).not.toThrow();
    expect(isAgentWorkspace('')).toBe(false);
  });

  it('returns true for paths containing agent keywords', () => {
    expect(isAgentWorkspace('/home/user/my-agent')).toBe(true);
    expect(isAgentWorkspace('/home/user/chatbot')).toBe(true);
    expect(isAgentWorkspace('/home/user/assistant-workspace')).toBe(true);
  });

  it('returns false for regular workspace paths', () => {
    expect(isAgentWorkspace('/home/user/my-project')).toBe(false);
  });
});
