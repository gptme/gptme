import '@testing-library/jest-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SuggestedActionsPanel } from '../SuggestedActionsPanel';
import type { Task } from '@/types/task';

const mockNavigate = jest.fn();
const mockArchiveTask = jest.fn();
const mockUnarchiveTask = jest.fn();

jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

// Server mutations are test doubles at the API boundary; the real react-query hooks run.
jest.mock('@/utils/taskApi', () => ({
  taskApi: {
    archiveTask: (...args: unknown[]) => mockArchiveTask(...args),
    unarchiveTask: (...args: unknown[]) => mockUnarchiveTask(...args),
  },
}));

const PR_URL = 'https://github.com/gptme/gptme/pull/42';
const REMOVED_LABELS = [
  'Start Task',
  'Pause Task',
  'Retry Task',
  'Edit Task',
  'Merge PR',
  'View Error Logs',
  'Edit & Retry',
];

const makeTask = (overrides: Partial<Task> = {}): Task => ({
  id: 'task-1',
  content: 'Test task',
  created_at: '2026-10-21T00:00:00Z',
  status: 'pending',
  target_type: 'stdout',
  conversation_ids: [],
  metadata: {},
  archived: false,
  ...overrides,
});

const pendingWithConversation = makeTask({
  conversation_ids: ['conv-old', 'conv-latest'],
  workspace: '/ws/task-1',
  git: { branch: 'b', clean: true, files: [], pr_url: PR_URL },
  target_type: 'pr',
  target_repo: 'gptme/gptme',
});

const FIXTURES: Array<{ name: string; task: Task }> = [
  { name: 'pending with conversation, PR, workspace', task: pendingWithConversation },
  { name: 'pending with no conversation', task: makeTask() },
  {
    name: 'archived pending',
    task: makeTask({ archived: true, conversation_ids: ['conv-1'], workspace: '/ws' }),
  },
  {
    name: 'active with conversation and PR',
    task: makeTask({
      status: 'active',
      conversation_ids: ['conv-1'],
      workspace: '/ws',
      git: { branch: 'b', clean: true, files: [], pr_url: PR_URL },
    }),
  },
  {
    name: 'active with no conversation or PR',
    task: makeTask({ status: 'active' }),
  },
  {
    name: 'failed with conversation',
    task: makeTask({ status: 'failed', conversation_ids: ['conv-1'] }),
  },
  { name: 'failed with no conversation', task: makeTask({ status: 'failed' }) },
  {
    name: 'completed PR still open',
    task: makeTask({
      status: 'completed',
      target_type: 'pr',
      target_repo: 'gptme/gptme',
      git: { branch: 'b', clean: true, files: [], pr_url: PR_URL, pr_status: 'OPEN' },
    }),
  },
  {
    name: 'completed PR merged and archived',
    task: makeTask({
      status: 'completed',
      target_type: 'pr',
      archived: true,
      git: {
        branch: 'b',
        clean: true,
        files: [],
        pr_url: PR_URL,
        pr_status: 'MERGED',
        pr_merged: true,
      },
    }),
  },
  {
    name: 'completed PR without PR URL but with target repo',
    task: makeTask({ status: 'completed', target_type: 'pr', target_repo: 'gptme/gptme' }),
  },
  {
    name: 'completed PR without PR URL or target repo',
    task: makeTask({ status: 'completed', target_type: 'pr' }),
  },
];

const renderPanel = (task: Task) => {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SuggestedActionsPanel task={task} />
    </QueryClientProvider>
  );
};

// Button accessible names are "<label> <description>", so match the label prefix.
const buttonFor = (label: string) =>
  screen.getByRole('button', { name: new RegExp(`^${label}(\\s|$)`) });

const offeredLabels = (): string[] =>
  screen
    .queryAllByRole('button')
    .map((button) => button.querySelector('.font-medium')?.textContent ?? '');

let openSpy: jest.SpyInstance;

const effectCount = (): number =>
  mockNavigate.mock.calls.length +
  openSpy.mock.calls.length +
  mockArchiveTask.mock.calls.length +
  mockUnarchiveTask.mock.calls.length;

describe('SuggestedActionsPanel', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockArchiveTask.mockResolvedValue({});
    mockUnarchiveTask.mockResolvedValue({});
    openSpy = jest.spyOn(window, 'open').mockImplementation(() => null);
  });

  afterEach(() => {
    openSpy.mockRestore();
  });

  describe('offered actions per status', () => {
    it('pending with conversation: navigation and archive only', () => {
      renderPanel(pendingWithConversation);
      expect(offeredLabels()).toEqual([
        'View Conversation',
        'View Pull Request',
        'Open Workspace',
        'Archive Task',
      ]);
    });

    it('pending conversation without workspace retains only conversation and archive', () => {
      renderPanel(makeTask({ conversation_ids: ['conv-1'] }));
      expect(offeredLabels()).toEqual(['View Conversation', 'Archive Task']);
    });

    it('pending with no conversation: only archive (no Start/Edit)', () => {
      renderPanel(makeTask());
      expect(offeredLabels()).toEqual(['Archive Task']);
    });

    it('pending with workspace but no conversation: only archive', () => {
      renderPanel(makeTask({ workspace: '/ws', conversation_ids: [] }));
      expect(offeredLabels()).toEqual(['Archive Task']);
    });

    it('archived pending: only unarchive', () => {
      renderPanel(makeTask({ archived: true, conversation_ids: ['conv-1'] }));
      expect(offeredLabels()).toEqual(['View Conversation', 'Unarchive Task']);
    });

    it('active with conversation and PR: PR, conversation, workspace (no Pause)', () => {
      renderPanel(FIXTURES[3].task);
      expect(offeredLabels()).toEqual(['View Pull Request', 'View Conversation', 'Open Workspace']);
    });

    it('active with no conversation or PR: offers nothing', () => {
      renderPanel(FIXTURES[4].task);
      expect(offeredLabels()).toEqual([]);
    });

    it('failed with conversation: only Debug in Workspace', () => {
      renderPanel(FIXTURES[5].task);
      expect(offeredLabels()).toEqual(['Debug in Workspace']);
    });

    it('failed with no conversation: explains that no actions are available', () => {
      renderPanel(FIXTURES[6].task);
      expect(offeredLabels()).toEqual([]);
      expect(screen.getByText('No actions available for this task.')).toBeInTheDocument();
    });

    it('completed PR still open: View PR and archive (no Merge)', () => {
      renderPanel(FIXTURES[7].task);
      expect(offeredLabels()).toEqual(['View Pull Request', 'Archive Task']);
    });

    it('completed PR merged and archived: View PR and unarchive', () => {
      renderPanel(FIXTURES[8].task);
      expect(offeredLabels()).toEqual(['View Pull Request', 'Unarchive Task']);
    });

    it('completed PR without PR URL but with repo: View PR falls back to repo pulls', () => {
      renderPanel(FIXTURES[9].task);
      expect(offeredLabels()).toEqual(['View Pull Request', 'Archive Task']);
    });

    it('completed PR without PR URL or repo: no View PR', () => {
      renderPanel(FIXTURES[10].task);
      expect(offeredLabels()).toEqual(['Archive Task']);
    });

    it.each(FIXTURES.map((f) => [f.name, f.task] as const))(
      'never offers unsupported actions: %s',
      (_name, task) => {
        renderPanel(task);
        for (const label of REMOVED_LABELS) {
          expect(offeredLabels()).not.toContain(label);
        }
      }
    );
  });

  describe('enabled actions have observable effects', () => {
    it.each(FIXTURES.map((f) => [f.name, f.task] as const))(
      'every offered button performs navigation or a mutation: %s',
      async (_name, task) => {
        const { unmount } = renderPanel(task);
        const labels = offeredLabels();
        unmount();

        for (const label of labels) {
          const { unmount: unmountOne } = renderPanel(task);
          const before = effectCount();
          fireEvent.click(buttonFor(label));
          // Mutations run asynchronously, so wait for the effect. Labelled so a failing click names the action.
          await waitFor(() => expect([label, effectCount() - before]).toEqual([label, 1]));
          unmountOne();
        }
      }
    );
  });

  describe('navigation', () => {
    it('View Conversation opens the latest conversation', () => {
      renderPanel(pendingWithConversation);
      fireEvent.click(buttonFor('View Conversation'));
      expect(mockNavigate).toHaveBeenCalledWith('/chat/conv-latest');
    });

    it('Open Workspace opens the workspace for the latest conversation', () => {
      renderPanel(pendingWithConversation);
      fireEvent.click(buttonFor('Open Workspace'));
      expect(mockNavigate).toHaveBeenCalledWith('/workspace/conv-latest');
    });

    it('Open Workspace does not need task.workspace', () => {
      renderPanel(makeTask({ status: 'active', conversation_ids: ['conv-1'] }));
      fireEvent.click(buttonFor('Open Workspace'));
      expect(mockNavigate).toHaveBeenCalledWith('/workspace/conv-1');
    });

    it('Debug in Workspace opens the workspace for the latest conversation', () => {
      renderPanel(FIXTURES[5].task);
      fireEvent.click(buttonFor('Debug in Workspace'));
      expect(mockNavigate).toHaveBeenCalledWith('/workspace/conv-1');
    });

    it('View Pull Request opens the PR URL in a new tab', () => {
      renderPanel(pendingWithConversation);
      fireEvent.click(buttonFor('View Pull Request'));
      expect(openSpy).toHaveBeenCalledWith(PR_URL, '_blank');
    });

    it('completed PR without PR URL falls back to repo pulls', () => {
      renderPanel(FIXTURES[9].task);
      fireEvent.click(buttonFor('View Pull Request'));
      expect(openSpy).toHaveBeenCalledWith('https://github.com/gptme/gptme/pulls', '_blank');
    });
  });

  describe('archive and unarchive', () => {
    it('Archive Task calls the archive API with the task id', async () => {
      renderPanel(makeTask());
      fireEvent.click(buttonFor('Archive Task'));
      await waitFor(() => expect(mockArchiveTask).toHaveBeenCalledWith('task-1'));
      expect(mockUnarchiveTask).not.toHaveBeenCalled();
    });

    it('Unarchive Task calls the unarchive API with the task id', async () => {
      renderPanel(FIXTURES[2].task);
      fireEvent.click(buttonFor('Unarchive Task'));
      await waitFor(() => expect(mockUnarchiveTask).toHaveBeenCalledWith('task-1'));
      expect(mockArchiveTask).not.toHaveBeenCalled();
    });
  });
});
