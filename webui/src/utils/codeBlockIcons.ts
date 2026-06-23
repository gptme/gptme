import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Terminal, Code, FileText, FileCode, Globe, Eye, Flag, SquareTerminal } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

/**
 * Shared icon vocabulary for code blocks. This is intentionally just a glyph
 * mapping (langtag → lucide icon) so it can be reused across the chat renderer
 * AND non-chat surfaces (tabbed code blocks, markdown previews) for visual
 * consistency — WITHOUT pulling in chat-message behaviors (collapse, tool-label
 * chrome), which stay in the chat renderer.
 *
 * Mirrors the icon set used by CollapsedStepGroup.
 */
export function iconForLangtag(langtag: string): LucideIcon {
  const tag = (langtag || '').split(' ')[0].toLowerCase();
  // file path (e.g. /path/to/file.py)
  if ((tag.includes('/') || tag.includes('\\') || tag.includes('.')) && tag === langtag) {
    return FileCode;
  }
  switch (tag) {
    case 'shell':
    case 'tmux':
    case 'sh':
    case 'bash':
    case 'zsh':
    case 'console':
      return Terminal;
    case 'ipython':
    case 'python':
      return Code;
    case 'save':
    case 'append':
    case 'patch':
    case 'morph':
    case 'read':
      return FileText;
    case 'browser':
    case 'search':
    case 'web':
      return Globe;
    case 'vision':
    case 'screenshot':
      return Eye;
    case 'stdout':
    case 'stderr':
    case 'result':
    case 'output':
      return SquareTerminal;
    case 'complete':
      return Flag;
    default:
      return Code;
  }
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/**
 * Build the inner HTML for a code-block label: a lucide icon followed by the
 * langtag text. Used by HTML-string renderers (smd custom renderer summary,
 * inline label, marked-based parseMarkdownContent).
 */
export function codeBlockLabelHtml(langtag: string, fallbackText = 'Code'): string {
  const Icon = iconForLangtag(langtag);
  const svg = renderToStaticMarkup(createElement(Icon, { size: 14 }));
  const text = langtag ? escapeHtml(langtag) : fallbackText;
  return `<span class="codeblock-icon">${svg}</span><span class="codeblock-label-text">${text}</span>`;
}
