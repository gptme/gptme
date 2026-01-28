import hljs from 'highlight.js';
//import 'highlight.js/styles/github-dark.css';

export interface HighlightResult {
  // The highlighted code
  code: string;
  // The detected language of the code
  language?: string;
}

/**
 * Highlight code with syntax highlighting
 * @param code The code to highlight
 * @param language Optional language specification
 * @param autoDetect Whether to attempt language auto-detection if language not specified
 * @param maxDetectionLength Maximum length for auto-detection (for performance)
 * @returns HighlightResult object with highlighted code and language
 */
export function highlightCode(
  code: string,
  language?: string,
  autoDetect = true,
  maxDetectionLength = 10000
): HighlightResult {
  if (!code) return { code: '' };

  try {
    // Normalize language name
    if (language) {
      // Handle common aliases
      if (language === 'shell') language = 'bash';
      if (language === 'result') language = 'markdown';

      // Check if language is supported
      if (hljs.getLanguage(language)) {
        return { code: hljs.highlight(code, { language }).value, language };
      }
    }

    // Auto-detect language for shorter code blocks if requested
    if (autoDetect && code.length < maxDetectionLength) {
      const result = hljs.highlightAuto(code);
      return { code: result.value, language: result.language };
    }

    // Return escaped plain text for larger blocks or when auto-detect is disabled
    return { code: code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') };
  } catch (error) {
    console.warn('Syntax highlighting error:', error);
    // Return escaped text on error
    return { code: code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') };
  }
}

/**
 * Detect language from filename or file extension
 * @param filename Filename or path
 * @returns Detected language or undefined
 */
export function detectLanguageFromFilename(filename: string): string | undefined {
  if (!filename) return undefined;

  // Extract extension
  const extension = filename.split('.').pop()?.toLowerCase();
  if (!extension) return undefined;

  // Map extensions to languages
  const extensionMap: Record<string, string> = {
    js: 'javascript',
    jsx: 'javascript',
    ts: 'typescript',
    tsx: 'typescript',
    py: 'python',
    rb: 'ruby',
    java: 'java',
    c: 'c',
    cpp: 'cpp',
    cs: 'csharp',
    go: 'go',
    rs: 'rust',
    php: 'php',
    html: 'html',
    css: 'css',
    scss: 'scss',
    json: 'json',
    md: 'markdown',
    yaml: 'yaml',
    yml: 'yaml',
    sh: 'bash',
    bash: 'bash',
    sql: 'sql',
  };

  return extensionMap[extension];
}

/**
 * Detect language from content heuristics
 * @param content Code content
 * @returns Detected language or undefined
 */
export function detectLanguageFromContent(content: string): string | undefined {
  if (!content) return undefined;

  const firstLine = content.split('\n')[0].trim();

  // Check for common patterns
  if (firstLine.startsWith('#!/bin/bash') || firstLine.startsWith('$')) return 'bash';
  if (
    firstLine.startsWith('#!/usr/bin/env python') ||
    firstLine.startsWith('import ') ||
    firstLine.startsWith('from ')
  )
    return 'python';
  if (
    firstLine.includes('function ') ||
    firstLine.includes('const ') ||
    firstLine.includes('let ') ||
    firstLine.includes('export ')
  )
    return 'javascript';
  if (firstLine.startsWith('using ') && firstLine.includes(';')) return 'csharp';
  if (firstLine.startsWith('package ') && firstLine.includes(';')) return 'java';

  return undefined;
}

/**
 * Detect language based on tool name, arguments, and content
 * @param tool Tool name (shell, ipython, etc.)
 * @param args Tool arguments (often includes filenames)
 * @param content Code content
 * @returns Detected language or undefined
 */
export function detectToolLanguage(
  tool?: string,
  args?: string[],
  content?: string
): string | undefined {
  if (!tool) return undefined;

  // Check by tool name first
  switch (tool.toLowerCase()) {
    case 'shell':
    case 'tmux':
      return 'bash';
    case 'ipython':
      return 'python';
    case 'patch':
      // For patch tool, check filename then default to diff
      return args && args[0] ? detectLanguageFromFilename(args[0]) || 'diff' : 'diff';
    case 'save':
    case 'append':
      // For save/append tools, check filename
      return args && args[0] ? detectLanguageFromFilename(args[0]) : undefined;
  }

  // Try to detect from content as fallback
  return content ? detectLanguageFromContent(content) : undefined;
}
