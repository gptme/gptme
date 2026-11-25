import { useState, useRef, useEffect } from 'react';
import { iconForLangtag } from '@/utils/codeBlockIcons';
import * as smd from '@/utils/smd';
import { customRenderer } from '@/utils/markdownRenderer';
import { staticHtmlPreviewSrcDoc } from '@/utils/remoteImages';

/**
 * Props for the TabbedCodeBlock component
 */
interface TabbedCodeBlockProps {
  /** The language or file extension of the code */
  language?: string;
  /** The raw code content to display */
  codeText: string;
  /** The code element to display */
  code: HTMLElement;
}

/**
 * TabbedCodeBlock renders a code block with tabs for switching between
 * code view and preview. Preview is only available for markdown and HTML content.
 *
 * For markdown content, it uses the streaming markdown parser to render the preview.
 * For HTML content, it uses a sandboxed iframe to prevent XSS attacks. Scripts
 * and remote content stay off until the user asks for them.
 */
export const TabbedCodeBlock: React.FC<TabbedCodeBlockProps> = ({ language, codeText, code }) => {
  const [activeTab, setActiveTab] = useState<'code' | 'preview'>('code');
  const previewRef = useRef<HTMLDivElement>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  // The exact content the user chose to run: new content needs a new click.
  const [allowedHtml, setAllowedHtml] = useState<string | null>(null);
  const isInteractive = allowedHtml === codeText;

  const LangIcon = iconForLangtag(language || '');

  // Check if this is a markdown code block
  const isMarkdown = language?.toLowerCase() === 'md' || language?.toLowerCase() === 'markdown';

  // Determine if preview should be available - only for markdown or HTML
  const hasPreview = isMarkdown || language?.toLowerCase() === 'html';

  // Handle markdown rendering when the preview tab is selected
  useEffect(() => {
    if (previewRef.current && codeText && isMarkdown) {
      try {
        // Clear previous content and error state
        previewRef.current.innerHTML = '';
        setRenderError(null);

        // Use streaming markdown parser in standard mode (no chat chrome)
        const renderer = customRenderer(previewRef.current, false, false, true, true);
        const parser = smd.parser(renderer);
        smd.parser_write(parser, codeText);
        smd.parser_end(parser);
      } catch (error) {
        console.error('Error rendering preview:', error);
        setRenderError('Failed to render preview');
      }
    }
  }, [codeText, language, isMarkdown]);

  return (
    <div>
      <div className="flex bg-gray-50 !p-0 dark:bg-gray-900">
        <button
          className={`px-4 py-2 text-xs font-normal transition-colors ${
            activeTab === 'code'
              ? 'border-b-2 border-blue-500 bg-white dark:bg-gray-800'
              : 'hover:bg-gray-100 dark:hover:bg-gray-800'
          }`}
          onClick={() => setActiveTab('code')}
        >
          <span className="inline-flex items-center gap-1">
            <LangIcon className="h-3.5 w-3.5" /> Code
          </span>
        </button>
        {hasPreview && (
          <button
            className={`px-4 py-2 text-xs font-normal transition-colors ${
              activeTab === 'preview'
                ? 'border-b-2 border-blue-500 bg-white dark:bg-gray-800'
                : 'hover:bg-gray-100 dark:hover:bg-gray-800'
            }`}
            onClick={() => setActiveTab('preview')}
          >
            👁️ Preview
          </button>
        )}
      </div>

      <div className="overflow-auto">
        <pre
          className={`m-0 overflow-auto ${activeTab === 'preview' ? '!hidden' : ''}`}
          dangerouslySetInnerHTML={{ __html: code.outerHTML }}
        />
        {renderError && activeTab === 'preview' && (
          <div className="rounded-md bg-red-50 p-4 text-sm text-red-600 dark:bg-red-900/20 dark:text-red-400">
            {renderError}
          </div>
        )}
        <div
          ref={previewRef}
          className={`preview-content prose prose-sm dark:prose-invert mx-4 !mt-[-10px] mb-4 max-w-none ${
            activeTab === 'code' || language === 'html' || renderError ? '!hidden' : ''
          }`}
        ></div>
        {/* Mount only on the Preview tab: a mounted iframe parses its srcDoc
            even while CSS keeps it hidden. The HTML is model output, so the
            default preview is static; running it can send data to any host
            (gptme/gptme#5223). */}
        {language === 'html' && activeTab === 'preview' && !renderError && (
          <>
            {!isInteractive && (
              <div className="flex flex-wrap items-center gap-2 px-4 py-2 text-xs text-muted-foreground">
                <span>Static preview: scripts and remote content are blocked.</span>
                <button
                  type="button"
                  className="rounded border px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-800"
                  title="The page will be able to send data to any website"
                  onClick={() => setAllowedHtml(codeText)}
                >
                  Run scripts and load remote content
                </button>
              </div>
            )}
            {/* Keyed so the new sandbox applies: it is read at navigation. */}
            <iframe
              key={isInteractive ? 'interactive' : 'static'}
              sandbox={isInteractive ? 'allow-scripts' : ''}
              srcDoc={isInteractive ? codeText : staticHtmlPreviewSrcDoc(codeText)}
              className="min-h-[450px] w-full border-0"
              title="HTML Preview"
            ></iframe>
          </>
        )}
      </div>
    </div>
  );
};
