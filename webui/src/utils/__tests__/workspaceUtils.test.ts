import { extractAgentsFromConversations, isAgentWorkspace } from '../workspaceUtils';

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

describe('agent identity', () => {
  it('keeps identical names and paths on different servers separate', () => {
    const agents = extractAgentsFromConversations([
      {
        id: 'a',
        name: 'First',
        modified: 1,
        agent_name: 'Bob',
        agent_path: '/agents/bob',
        serverId: 'one',
      },
      {
        id: 'b',
        name: 'Second',
        modified: 2,
        agent_name: 'Bob',
        agent_path: '/agents/bob',
        serverId: 'two',
      },
      {
        id: 'c',
        name: 'Third',
        modified: 3,
        agent_name: 'Bob',
        agent_path: '/other/bob',
        serverId: 'one',
      },
    ]);
    expect(agents).toHaveLength(3);
    expect(agents.map((agent) => agent.conversationCount)).toEqual([1, 1, 1]);
  });
});
