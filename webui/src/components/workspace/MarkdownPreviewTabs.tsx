import { useState, useRef, useEffect } from 'react';
import { getCodeBlockEmoji } from '@/utils/markdownUtils';
import * as smd from '@/utils/smd';
import { customRenderer } from '@/utils/markdownRenderer';
import { CodeDisplay } from '@/components/CodeDisplay';

interface MarkdownPreviewTabsProps {
  content: string;
  language?: string;
}

export function MarkdownPreviewTabs({ content, language = 'markdown' }: MarkdownPreviewTabsProps) {
  const [activeTab, setActiveTab] = useState<'code' | 'preview'>('preview');
  const previewRef = useRef<HTMLDivElement>(null);
  const [renderError, setRenderError] = useState<string | null>(null);

  const emoji = getCodeBlockEmoji(language);

  // Handle markdown rendering when the preview tab is selected
  useEffect(() => {
    if (previewRef.current && content && activeTab === 'preview') {
      try {
        // Clear previous content and error state
        previewRef.current.innerHTML = '';
        setRenderError(null);

        // Use streaming markdown parser for markdown content
        const renderer = customRenderer(previewRef.current);
        const parser = smd.parser(renderer);
        smd.parser_write(parser, content);
        smd.parser_end(parser);
      } catch (error) {
        console.error('Error rendering markdown preview:', error);
        setRenderError('Failed to render markdown preview');
      }
    }
  }, [content, activeTab]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex border-b bg-gray-50 dark:bg-gray-900" role="tablist">
        <button
          role="tab"
          aria-selected={activeTab === 'preview'}
          aria-controls="preview-panel"
          id="preview-tab"
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === 'preview'
              ? 'border-b-2 border-blue-500 bg-white dark:bg-gray-800'
              : 'hover:bg-gray-100 dark:hover:bg-gray-800'
          }`}
          onClick={() => setActiveTab('preview')}
        >
          👁️ Preview
        </button>
        <button
          role="tab"
          aria-selected={activeTab === 'code'}
          aria-controls="code-panel"
          id="code-tab"
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === 'code'
              ? 'border-b-2 border-blue-500 bg-white dark:bg-gray-800'
              : 'hover:bg-gray-100 dark:hover:bg-gray-800'
          }`}
          onClick={() => setActiveTab('code')}
        >
          {emoji} Code
        </button>
      </div>

      <div className="flex-1 overflow-auto">
        {activeTab === 'code' && (
          <div role="tabpanel" id="code-panel" aria-labelledby="code-tab">
            <CodeDisplay code={content} language={language} maxHeight="none" />
          </div>
        )}

        {activeTab === 'preview' && (
          <div role="tabpanel" id="preview-panel" aria-labelledby="preview-tab">
            {renderError ? (
              <div className="p-4">
                <div className="rounded-md bg-red-50 p-4 text-sm text-red-600 dark:bg-red-900/20 dark:text-red-400">
                  {renderError}
                </div>
              </div>
            ) : (
              <div ref={previewRef} className="prose prose-sm dark:prose-invert max-w-none p-4" />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
