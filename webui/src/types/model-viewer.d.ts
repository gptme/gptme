// JSX intrinsic element declaration for @google/model-viewer web component.
// The package registers the <model-viewer> custom element; this extends React's
// type system so JSX can reference it without an "unknown element" error.

import 'react';

// React 19 scopes JSX to React rather than the global JSX namespace.
declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      'model-viewer': React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        src?: string;
        'camera-controls'?: boolean;
        'auto-rotate'?: boolean;
        ar?: boolean;
        'ar-modes'?: string;
        style?: React.CSSProperties;
      };
    }
  }
}
