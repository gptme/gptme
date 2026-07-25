export default {
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
    '\\.(css|less|sass|scss)$': 'identity-obj-proxy',
    '^@google/model-viewer$': '<rootDir>/src/__mocks__/@google/model-viewer.ts',
  },
  transform: {
    '^.+\\.(ts|tsx|js|jsx)$': [
      'ts-jest',
      {
        tsconfig: 'tsconfig.test.json',
      },
    ],
  },
  testPathIgnorePatterns: ['/node_modules/', '/e2e/'],
  transformIgnorePatterns: ['/node_modules/(?!(ansi-regex|pretty-format|@testing-library)/)'],
};
