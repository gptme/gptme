import * as smd from '@/utils/smd';
import { highlightCode } from '@/utils/highlightUtils';
import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { Brain } from 'lucide-react';
import { codeBlockLabelHtml } from '@/utils/codeBlockIcons';
import { TabbedCodeBlock } from '@/components/TabbedCodeBlock';

/**
 * Langtags whose blocks are collapsed by default: actual tool-use (executing
 * tools, file writes, etc.) and tool output. Deliberately excludes highlight-only
 * langs like `python`/`sh` — those are agent code examples shown to the user, so
 * they stay open. (Note: this differs from toolCallParser's GPTME_TOOL_ALLOWLIST,
 * which includes `python` for tool-call detection.)
 */
const COLLAPSE_BY_DEFAULT = new Set([
  // tool-use
  'shell',
  'tmux',
  'ipython',
  'save',
  'append',
  'patch',
  'morph',
  'read',
  'browser',
  'vision',
  'screenshot',
  'gh',
  'mcp',
  'subagent',
  // tool output
  'stdout',
  'stderr',
  'result',
  'output',
]);

function collapsesByDefault(langtag: string): boolean {
  return COLLAPSE_BY_DEFAULT.has((langtag || '').split(' ')[0].toLowerCase());
}

/**
 * Checks if the language is markdown or html
 * @param language - The language of the code block
 * @returns True if the language is markdown or html, false otherwise
 */
function isMarkdownOrHtml(language: string | null | undefined): boolean {
  return (
    language?.toLowerCase() === 'md' ||
    language?.toLowerCase() === 'markdown' ||
    language?.toLowerCase() === 'html'
  );
}

/**
 * CustomRendererData extends the default renderer data with additional properties
 * for tracking code blocks, their language, and content.
 */
export type CustomRendererData = smd.Default_Renderer_Data & {
  placeholder: HTMLElement | null;
  summary: HTMLElement | null;
  code: HTMLElement | null;
  lang: string | null;
  codeText: string; // Store code text for preview
};

/**
 * CustomRenderer type extends the default renderer with our own implementation
 * of the render methods.
 */
export type CustomRenderer = Omit<
  smd.Default_Renderer,
  'add_token' | 'end_token' | 'set_attr' | 'add_text'
> & {
  add_token: (data: CustomRendererData, type: smd.Token) => void;
  end_token: (data: CustomRendererData) => void;
  set_attr: (data: CustomRendererData, type: smd.Attr, value: string) => void;
  add_text: (data: CustomRendererData, text: string) => void;
  data: CustomRendererData;
};

/**
 * Creates a custom renderer for streaming markdown that can optionally render
 * code blocks as React components with tabs for viewing code and its preview.
 *
 * @param root - The HTML element to render content into
 * @param log - Whether to log rendering details (for debugging)
 * @param useReactTabbed - Whether to use React components for code blocks with tabs
 * @param blocksDefaultOpen - Whether code blocks should be open by default
 * @param standardMarkdown - When true, renders plain code blocks without chat-specific
 *   chrome (no collapsible details, no tool labels/icons). Use for non-chat contexts
 *   like workspace file previews and artifact viewers.
 * @returns A CustomRenderer instance
 */
export function customRenderer(
  root: HTMLElement,
  log: boolean = false,
  useReactTabbed: boolean = false,
  blocksDefaultOpen: boolean = true,
  standardMarkdown: boolean = false
): CustomRenderer {
  return {
    add_token: (data: CustomRendererData, type: smd.Token) => {
      if (log) {
        console.log('add_token:', smd.token_to_string(type));
      }
      let parent = data.nodes[data.index];
      let slot;

      switch (type) {
        case smd.CODE_BLOCK:
        case smd.CODE_FENCE: {
          if (standardMarkdown) {
            // Standard mode: plain pre+code, no collapsible details/summary chrome
            parent = parent.appendChild(document.createElement('pre'));
            slot = document.createElement('code');
            data.code = slot;
            slot.setAttribute('class', 'hljs');
            data.nodes[++data.index] = parent.appendChild(slot);
            break;
          }

          parent = parent.appendChild(document.createElement('details'));
          // Default to open (agent code shown to the user). Tool-use/output
          // blocks are collapsed once their langtag is known (see smd.LANG),
          // unless the blocksDefaultOpen override forces everything open.
          parent.setAttribute('open', 'true');
          data.summary = parent.appendChild(document.createElement('summary'));
          data.summary.innerHTML = codeBlockLabelHtml('');

          if (useReactTabbed) {
            // Create placeholder element to be replaced with React component later
            const placeholderDiv = document.createElement('div');
            placeholderDiv.className = 'tabbed-code-block-placeholder !p-0';
            data.placeholder = placeholderDiv;
            data.codeText = ''; // Initialize codeText for this block
            parent = parent.appendChild(placeholderDiv);
          }

          parent = parent.appendChild(document.createElement('pre'));
          slot = document.createElement('code');
          data.code = slot;
          slot.setAttribute('class', 'hljs');
          data.nodes[++data.index] = parent.appendChild(slot);
          // }
          break;
        }
        case smd.THINKING_SUMMARY: {
          // Render the thinking summary with a lucide icon (smd.js sets the text
          // "Thinking" right after via add_text), matching the code-block labels.
          const summary = document.createElement('summary');
          summary.innerHTML = `<span class="codeblock-icon">${renderToStaticMarkup(
            createElement(Brain, { size: 14 })
          )}</span>`;
          data.nodes[++data.index] = parent.appendChild(summary);
          break;
        }
        default:
          smd.default_add_token(data, type);
          return;
      }
    },
    end_token: (data: CustomRendererData) => {
      if (log) {
        console.log('end_token');
      }

      // Run syntax highlight exactly once on the complete code text (O(n) per block).
      // add_text now accumulates raw text and shows escaped HTML during streaming;
      // this pass replaces it with properly highlighted HTML at block completion.
      if (data.code && data.codeText) {
        const langFromInfo = data.lang ? data.lang.split('.').pop() : undefined;
        const highlighted = highlightCode(data.codeText, langFromInfo, true);
        if (highlighted.language) {
          data.lang = highlighted.language;
          data.code.setAttribute('class', `hljs language-${highlighted.language}`);
        }
        data.code.innerHTML = highlighted.code;
      }

      if (!standardMarkdown) {
        if (useReactTabbed && data.placeholder && data.code && data.lang && data.codeText) {
          const langFromInfo = data.lang ? data.lang.split('.').pop() : undefined;
          // Only render for markdown and html
          if (!isMarkdownOrHtml(langFromInfo)) return;
          try {
            // Create React root and render the component
            const reactRoot = createRoot(data.placeholder);
            reactRoot.render(
              createElement(TabbedCodeBlock, {
                language: langFromInfo,
                codeText: data.codeText,
                code: data.code!,
              })
            );
          } catch (error) {
            console.error('Error rendering TabbedCodeBlock:', error);
          }
        }

        // Convert short code blocks (≤2 lines) from <details> to inline display.
        // Long lines scroll horizontally; the label stays fixed on the left.
        if (data.code && data.summary) {
          const codeText = (data.code.textContent || '').replace(/\n$/, '');
          const lineCount = codeText.split('\n').length;
          const details = data.summary.parentElement; // the <details> element

          if (lineCount <= 2 && codeText.length <= 1000 && details?.tagName === 'DETAILS') {
            // Replace <details><summary>...<pre><code>... with inline element
            const inline = document.createElement('div');
            inline.className = 'inline-codeblock';
            inline.innerHTML =
              `<span class="inline-codeblock-label">${data.summary.innerHTML}</span>` +
              `<code class="${data.code.className}">${data.code.innerHTML}</code>`;
            details.replaceWith(inline);
          }
        }
      }

      data.summary = null;
      data.lang = null;
      data.code = null;
      data.codeText = '';
      smd.default_end_token(data);
    },
    add_text: (data: CustomRendererData, text: string) => {
      if (log) {
        console.log('add_text:', text);
      }

      if (data.code) {
        // Accumulate raw text; full syntax highlight runs once in end_token
        // (previously re-highlighted the whole block on every token → O(n²))
        data.codeText += text;

        // Null the TabbedCodeBlock placeholder early for non-markdown/html blocks
        // so end_token skips the TabbedCodeBlock path (which has an early return
        // that would bypass cleanup). Language is known from set_attr(LANG) which
        // fires before add_text for explicitly-tagged fences.
        if (useReactTabbed && data.placeholder) {
          const langFromInfo = data.lang ? data.lang.split('.').pop() : undefined;
          // Normalize 'result' alias — highlightCode maps it to 'markdown', so
          // treat it as markdown here too to preserve TabbedCodeBlock rendering.
          const normalizedLang = langFromInfo === 'result' ? 'markdown' : langFromInfo;
          if (!isMarkdownOrHtml(normalizedLang)) {
            data.placeholder = null;
          }
        }

        // Display escaped text incrementally during streaming
        data.code.innerHTML = data.codeText
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;');
      } else {
        smd.default_add_text(data, text);
      }
    },
    set_attr: (data: CustomRendererData, type: smd.Attr, value: string) => {
      if (log) {
        console.log('set_attr:', smd.attr_to_html_attr(type), value);
      }

      // Set the language in the summary tag
      if (type === smd.LANG) {
        data.lang = value;
        if (!standardMarkdown && data.summary) {
          // Code-block label — CHAT renderer (smd custom renderer, real DOM nodes).
          // This is the path the chat view (/chat/...) actually uses. We build a
          // lucide icon + langtag label as innerHTML (carried over to the inline
          // label below). The NON-CHAT path (marked) in markdownUtils.ts still uses
          // getCodeBlockEmoji; keep the two in sync when changing labels.
          data.summary.innerHTML = codeBlockLabelHtml(value);

          // Smart collapse: tool-use and tool-output blocks are for
          // inspection/review, so collapse them by default. Agent code examples
          // (```python, ```sh, file content, …) stay open. blocksDefaultOpen is
          // an override that force-expands everything.
          if (!blocksDefaultOpen && collapsesByDefault(value)) {
            const details = data.summary.parentElement;
            if (details?.tagName === 'DETAILS') {
              details.removeAttribute('open');
            }
          }
        }
        if (data.code) {
          const langFromInfo = value ? value.split('.').pop() : undefined;
          data.code.setAttribute('class', `hljs ${langFromInfo ? `language-${langFromInfo}` : ''}`);
        }
      } else if (type === smd.HREF) {
        smd.default_set_attr(data, type, value);
        // Open external links in new tab
        const node = data.nodes[data.index];
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
      } else {
        smd.default_set_attr(data, type, value);
      }
    },
    data: {
      nodes: [root],
      index: 0,
      placeholder: null,
      summary: null,
      lang: null,
      code: null,
      codeText: '',
    },
  };
}
