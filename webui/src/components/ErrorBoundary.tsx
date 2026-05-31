import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  isChunkLoadError: boolean;
}

function isChunkLoadError(error: Error): boolean {
  return (
    error.name === 'ChunkLoadError' ||
    /Loading chunk .* failed/.test(error.message) ||
    /Failed to fetch dynamically imported module/.test(error.message)
  );
}

/** Global error fallback shown when a chunk fails to load or a component crashes. */
function ErrorFallback({ error, onReload }: { error: Error | null; onReload: () => void }) {
  const isChunk = error ? isChunkLoadError(error) : false;
  return (
    <div className="flex min-h-[400px] flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="text-4xl">⚠️</div>
      <h2 className="text-xl font-semibold">
        {isChunk ? 'Failed to load page' : 'Something went wrong'}
      </h2>
      <p className="max-w-md text-sm text-muted-foreground">
        {isChunk
          ? 'A required script failed to load. This can happen after an update — reloading usually fixes it.'
          : 'An unexpected error occurred. Reloading the page may help.'}
      </p>
      {error && !isChunk && (
        <pre className="max-w-md overflow-auto rounded bg-muted px-4 py-2 text-left text-xs text-muted-foreground">
          {error.message}
        </pre>
      )}
      <button
        onClick={onReload}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
      >
        Reload page
      </button>
    </div>
  );
}

/**
 * Catches render errors and chunk-load failures anywhere in the component tree,
 * preventing the entire app from going blank. Shows a recovery UI with a reload button.
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, isChunkLoadError: false };
  }

  static getDerivedStateFromError(error: Error): State {
    return {
      hasError: true,
      error,
      isChunkLoadError: isChunkLoadError(error),
    };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, info.componentStack);
  }

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return <ErrorFallback error={this.state.error} onReload={this.handleReload} />;
    }
    return this.props.children;
  }
}
