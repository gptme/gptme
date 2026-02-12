import { getCodeBlockEmoji } from '@/utils/markdownUtils';
import * as smd from '@/utils/smd';
import { highlightCode } from '@/utils/highlightUtils';
import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { TabbedCodeBlock } from '@/components/TabbedCodeBlock';

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
 * @returns A CustomRenderer instance
 */
export function customRenderer(
  root: HTMLElement,
  log: boolean = false,
  useReactTabbed: boolean = false,
  blocksDefaultOpen: boolean = true
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
          parent = parent.appendChild(document.createElement('details'));
          if (blocksDefaultOpen) {
            parent.setAttribute('open', 'true');
          }
          data.summary = parent.appendChild(document.createElement('summary'));
          data.summary.textContent = '💻 Code';

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
        default:
          smd.default_add_token(data, type);
          return;
      }
    },
    end_token: (data: CustomRendererData) => {
      if (log) {
        console.log('end_token');
      }

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
        // Highlight code blocks
        const lang = data.lang;
        const langFromInfo = lang ? lang.split('.').pop() : undefined;
        const previousText = data.nodes[data.index].textContent;
        const newText = previousText + text;
        const highlighted = highlightCode(newText, langFromInfo, true);
        // Update the language if it was detected
        if (highlighted.language) {
          data.lang = highlighted.language;
        }
        data.nodes[data.index].innerHTML = highlighted.code;

        // Store code text for React tabbed component
        if (useReactTabbed && data.placeholder && isMarkdownOrHtml(highlighted.language)) {
          data.codeText += text;
        } else {
          data.placeholder = null;
        }
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
        if (data.summary) {
          const emoji = getCodeBlockEmoji(value);
          data.summary.textContent = `${emoji} ${value}`;
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
