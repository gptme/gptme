# gptme-webui

A fancy web UI for [gptme][gptme].

## Features

- Chat with LLMs using gptme, just like in the CLI, but with a fancy UI
- Generate responses and run tools by connecting to gptme-server instances
- **Multi-backend**: connect to multiple servers simultaneously and see all conversations in a unified view
- Read bundled demo conversations without running gptme locally (useful for sharing)

## Deployment Modes

The web UI is used in several different configurations:

| Mode | Description | Server |
|------|-------------|--------|
| **Local dev** | Run alongside `gptme-server` for local development | `http://127.0.0.1:5700` |
| **Desktop app** | Bundled in [gptme-tauri](https://github.com/gptme/gptme-tauri) as a native desktop app | Local gptme-server embedded |
| **Hosted (open)** | Hosted at [chat.gptme.org](https://chat.gptme.org/) — bring your own server | User-configured |
| **Cloud** | Managed service at [gptme.ai](https://gptme.ai) — no server setup needed | `https://api.gptme.ai` |
| **Custom remote** | Connect to remote servers (VMs, workstations, agent instances) | User-configured |

All modes use the same codebase. The multi-backend feature lets you connect to several of these simultaneously.

### Running locally

```sh
cd webui
npm i
npm run dev       # starts at http://localhost:5701

# in another terminal
pipx install 'gptme[server]'
gptme-server --cors-origin='http://localhost:5701'
```

## Multi-Backend Support

The web UI can connect to multiple gptme servers at once, showing conversations from all connected servers in a unified sidebar.

### Pre-configured servers

Two servers are available out of the box:

| Name | URL | Description |
|------|-----|-------------|
| **Local** | `http://127.0.0.1:5700` | Your local gptme-server |
| **Cloud** | `https://api.gptme.ai` | gptme.ai managed service (requires auth) |

Additional servers can be added in **Settings > Servers** (e.g. agent VMs, remote workstations).

### How it works

- **Unified conversation list**: When connected to multiple servers, conversations from all servers are merged into one sorted list in the sidebar.
- **Server labels**: Each conversation shows a small label indicating which server it's from — but only when conversations from 2+ servers are visible. With a single server, labels are hidden (it's implicit).
- **Server selector on new chat**: When multiple servers are connected, the new conversation input shows a dropdown to pick which server to create the conversation on.
- **Quick switching**: The header shows a server dropdown for managing connections (connect/disconnect servers, add new ones).
- **Settings panel**: Full CRUD for server configurations under Settings > Servers.

### Architecture

```
ServerRegistry (stores/servers.ts)
  └─ ServerConfig[] persisted to localStorage
       ├─ Local  (pre-configured, default)
       ├─ Cloud  (pre-configured)
       └─ custom servers added by user

ApiContext (contexts/ApiContext.tsx)
  └─ manages active ApiClient for the primary server
       └─ handles connection, auto-reconnect, auth

useMultiServerConversations (hooks/)
  └─ fetches conversation lists from all connected servers in parallel
       └─ tags each ConversationSummary with serverId
            └─ merged + sorted in the unified sidebar

ConversationList
  └─ renders server label per conversation (when multi-server)
```

### Data model

```typescript
interface ServerConfig {
  id: string;
  name: string;          // "Local", "Cloud", "bob-vm", etc.
  baseUrl: string;
  authToken: string | null;
  useAuthToken: boolean;
  isPreset?: boolean;    // pre-configured servers can't be deleted
}

// ConversationSummary gets a serverId field
interface ConversationSummary {
  id: string;
  serverId?: string;     // which server this conversation is from
  serverName?: string;   // display name for the label
  // ... existing fields
}
```

## Tech stack

- Vite
- TypeScript
- React
- shadcn-ui / Tailwind CSS
- [Legend State](https://legendapp.com/open-source/state/) for reactive state
- [TanStack Query](https://tanstack.com/query) for server data fetching

## Development

```sh
npm run dev             # development server
npm run build           # production build (includes type checking)
npm run lint            # linting + type checking
npm run typecheck       # type checking only
npm run typecheck:watch # type checking in watch mode
```

## Testing

```sh
npm test                # all tests
npm run test:watch      # watch mode
npm run test:coverage   # with coverage
npm run test:e2e        # Playwright end-to-end tests
```

[gptme]: https://github.com/gptme/gptme
