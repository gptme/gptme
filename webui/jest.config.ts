export default {
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  moduleNameMapper: {
    // Must beat the @/ mapper: Jest cannot parse import.meta in viteEnv.ts.
    '^@/utils/viteEnv$': '<rootDir>/src/utils/__mocks__/viteEnv.ts',
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
  // Ignore Playwright specs under e2e/, but allow Jest tests in e2e/helpers/.
  testPathIgnorePatterns: ['/node_modules/', '/e2e/(?!helpers/)'],
  transformIgnorePatterns: ['/node_modules/(?!(ansi-regex|pretty-format|@testing-library)/)'],
};
