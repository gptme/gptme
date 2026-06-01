import { describe, it, expect } from '@jest/globals';
import {
  getToolSummary,
  getToolCategory,
  CATEGORY_COLORS,
  CATEGORY_BORDER_ONLY,
} from '../toolCallParser';

describe('toolCallParser', () => {
  describe('getToolCategory', () => {
    it.each([
      ['save', 'file'],
      ['append', 'file'],
      ['patch', 'file'],
      ['morph', 'file'],
      ['read', 'file'],
      ['ls', 'file'],
      ['shell', 'shell'],
      ['tmux', 'shell'],
      ['ipython', 'code'],
      ['python', 'code'],
      ['browser', 'browser'],
      ['chrome-devtools', 'browser'],
      ['vision', 'vision'],
      ['screenshot', 'vision'],
      ['unknown_tool', 'generic'],
    ])('maps %s to %s', (tool, expected) => {
      expect(getToolCategory(tool)).toBe(expected);
    });
  });

  describe('getToolSummary', () => {
    it('uses first arg when available and short', () => {
      const summary = getToolSummary({
        tool: 'save',
        args: ['/path/to/file.ts'],
        content: 'export const foo = 1;',
      });
      expect(summary).toBe('/path/to/file.ts');
    });

    it('truncates long args', () => {
      const veryLong = 'a'.repeat(80);
      const summary = getToolSummary({
        tool: 'save',
        args: [veryLong],
        content: '',
      });
      expect(summary.length).toBeLessThanOrEqual(60);
      expect(summary.endsWith('...')).toBe(true);
    });

    it('falls back to content when no args', () => {
      const summary = getToolSummary({
        tool: 'shell',
        args: [],
        content: 'echo "hello world"',
      });
      expect(summary).toBe('echo "hello world"');
    });

    it('falls back to tool name when empty', () => {
      const summary = getToolSummary({
        tool: 'unknown',
        args: [],
        content: '',
      });
      expect(summary).toBe('unknown');
    });

    it('truncates long content lines', () => {
      const veryLong = 'echo ' + 'x'.repeat(100);
      const summary = getToolSummary({
        tool: 'shell',
        args: [],
        content: veryLong,
      });
      expect(summary.length).toBeLessThanOrEqual(80);
      expect(summary.endsWith('...')).toBe(true);
    });
  });

  describe('category color maps', () => {
    it('has entries for all categories', () => {
      const categories = ['file', 'shell', 'code', 'browser', 'vision', 'generic'] as const;
      for (const cat of categories) {
        expect(CATEGORY_COLORS[cat]).toBeTruthy();
        expect(CATEGORY_BORDER_ONLY[cat]).toBeTruthy();
      }
    });
  });
});
