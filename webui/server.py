#!/usr/bin/env python3
"""Simple HTTP server with SPA routing support.

Serves static files and falls back to index.html for all other routes,
enabling proper single-page application routing.
"""

import http.server
import socketserver
import os
from pathlib import Path


class SPAHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler with SPA routing support."""

    def do_GET(self):
        """Handle GET requests with SPA fallback."""
        # Get the requested path
        path = self.translate_path(self.path)
        
        # If path is a directory, try index.html
        if os.path.isdir(path):
            index_path = os.path.join(path, 'index.html')
            if os.path.exists(index_path):
                path = index_path
        
        # If file doesn't exist and it's not an asset, serve index.html
        if not os.path.exists(path):
            # Check if this looks like an asset request
            is_asset = any(self.path.endswith(ext) for ext in 
                          ['.js', '.css', '.png', '.jpg', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.eot', '.otf', '.json', '.webp', '.gif'])
            
            if not is_asset:
                # Serve index.html for SPA routes
                self.path = '/index.html'
        
        # Call parent handler
        return http.server.SimpleHTTPRequestHandler.do_GET(self)


def run_server(port=5701, bind='0.0.0.0'):
    """Run the HTTP server."""
    with socketserver.TCPServer((bind, port), SPAHTTPRequestHandler) as httpd:
        print(f"Serving on {bind}:{port}")
        print("SPA routing enabled - serving index.html for non-asset routes")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5701
    run_server(port=port)
