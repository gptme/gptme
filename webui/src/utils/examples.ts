export type UserType = 'technical' | 'non-technical' | 'mixed';
export type ExampleCategory =
  | 'chat-input-placeholders'
  | 'welcome-suggestions'
  | 'environment-variables'
  | 'mcp-server-names'
  | 'mcp-server-commands'
  | 'task-creation';

interface ExampleSet {
  technical: string[];
  'non-technical': string[];
  mixed: string[];
}

const EXAMPLES: Record<ExampleCategory, ExampleSet> = {
  'chat-input-placeholders': {
    technical: [
      'Write a Python script to analyze CSV data',
      'Debug my React component and fix TypeScript errors',
      'Create a REST API with FastAPI and add tests',
      'Help me refactor this code to be more readable',
      'Set up a CI/CD pipeline for my project',
    ],
    'non-technical': [
      'Help me organize my files and folders',
      'Write a simple script to automate a task',
      'Explain this code in plain English',
      'Create a basic website for my project',
      'Help me learn programming concepts',
    ],
    mixed: [
      'Write a Python script to analyze my data',
      "Debug this error I'm getting",
      'Create a simple web application',
      'Help me understand this code better',
      'Set up my development environment',
    ],
  },
  'welcome-suggestions': {
    technical: [
      'Write a Python script',
      'Debug TypeScript errors',
      'Set up CI/CD pipeline',
      'Refactor existing code',
      'Create API endpoints',
      'Generate unit tests',
      'Optimize performance',
      'Review code quality',
    ],
    'non-technical': [
      'Learn programming basics',
      'Create a simple website',
      'Organize my files',
      'Write documentation',
      'Explain this code',
      'Get started with Git',
      'Set up my workspace',
      'Plan my project',
    ],
    mixed: [
      'Write a Python script',
      'Debug this error',
      'Explore my project',
      'Generate tests',
      'Create documentation',
      'Organize files',
      'Learn new concepts',
      'Build something cool',
    ],
  },
  'environment-variables': {
    technical: ['DATABASE_URL', 'REDIS_URL', 'JWT_SECRET', 'API_BASE_URL', 'NODE_ENV'],
    'non-technical': ['API_KEY', 'USERNAME', 'PASSWORD', 'EMAIL', 'WORKSPACE_PATH'],
    mixed: ['API_KEY', 'DATABASE_URL', 'USERNAME', 'WORKSPACE_PATH', 'NODE_ENV'],
  },
  'mcp-server-names': {
    technical: [
      'filesystem-server',
      'git-integration',
      'database-client',
      'api-gateway',
      'docker-manager',
    ],
    'non-technical': [
      'file-helper',
      'document-manager',
      'backup-service',
      'photo-organizer',
      'note-taker',
    ],
    mixed: ['file-manager', 'git-helper', 'backup-tool', 'api-client', 'workspace-sync'],
  },
  'mcp-server-commands': {
    technical: [
      'python -m uvicorn server:app',
      'node server.js',
      'cargo run --bin server',
      'go run cmd/server/main.go',
      'java -jar server.jar',
    ],
    'non-technical': [
      'python app.py',
      'node index.js',
      'npm start',
      'python -m http.server',
      './run.sh',
    ],
    mixed: ['python server.py', 'npm start', 'node app.js', 'python -m app', './start.sh'],
  },
  'task-creation': {
    technical: [
      'Implement authentication with JWT tokens',
      'Add unit tests for the user service',
      'Set up Docker containerization',
      'Optimize database queries for performance',
      'Create REST API endpoints for CRUD operations',
    ],
    'non-technical': [
      'Organize project documentation',
      'Create a simple contact form',
      'Set up a basic website',
      'Write user-friendly README file',
      'Create a backup of important files',
    ],
    mixed: [
      'Build a todo list application',
      'Create a simple dashboard',
      'Set up project structure',
      'Write documentation and tests',
      'Deploy application to production',
    ],
  },
};

/**
 * Get examples for a specific category and user type
 */
export function getExamples(
  category: ExampleCategory,
  userType: UserType = 'mixed',
  count?: number
): string[] {
  const examples = EXAMPLES[category]?.[userType] || EXAMPLES[category]?.mixed || [];

  if (count !== undefined) {
    return examples.slice(0, count);
  }

  return examples;
}

/**
 * Get a single random example for a specific category and user type
 * Uses time-based selection for consistency within a time period
 */
export function getRandomExample(
  category: ExampleCategory,
  userType: UserType = 'mixed',
  timeWindow: 'hour' | 'day' | 'minute' = 'hour'
): string {
  const examples = getExamples(category, userType);

  if (examples.length === 0) {
    return 'Ask me anything...';
  }

  // Use different time windows for different rotation speeds
  let timeValue: number;
  switch (timeWindow) {
    case 'minute':
      timeValue = new Date().getMinutes();
      break;
    case 'hour':
      timeValue = new Date().getHours();
      break;
    case 'day':
      timeValue = new Date().getDate();
      break;
  }

  const index = timeValue % examples.length;
  return examples[index];
}

/**
 * Get examples with weights/preferences for certain examples
 */
export function getWeightedExamples(
  category: ExampleCategory,
  userType: UserType = 'mixed',
  weights?: Record<string, number>
): string[] {
  const examples = getExamples(category, userType);

  if (!weights) {
    return examples;
  }

  // Sort examples by weight (higher weight = more likely to appear first)
  return examples.sort((a, b) => (weights[b] || 0) - (weights[a] || 0));
}
