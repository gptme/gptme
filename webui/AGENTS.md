# Web UI Agent Guide

Architecture notes and gotchas for agents working on the gptme web UI.

## Dev Setup & Testing

Start the backend and frontend dev servers:

```bash
# Terminal 1 — backend (from repo root)
uv pip install -e ".[server]"
uv run gptme-server --cors-origin='http://localhost:5701'

# Terminal 2 — frontend
cd webui && npm i && npm run dev
```

The UI is at `http://localhost:5701`. The backend defaults to port 5700.

### Running tests

```bash
cd webui
npm test                  # Unit tests (Jest)
npm run test:e2e          # Playwright E2E (starts its own dev server)
npm run test:e2e:ui       # Playwright with interactive UI
npm run typecheck         # TypeScript check
npm run lint              # ESLint + typecheck
```

### Visual verification with Playwright

For UI changes, take screenshots to verify rendering:

```typescript
// In a Playwright test or script
await page.goto('http://localhost:5701/chat/<conversation-id>?server=<server-id>');
await page.screenshot({ path: 'screenshot.png', fullPage: true });
```

You can navigate directly to a conversation URL to test specific scenarios. The server ID is shown in the browser's connection config.

## Rendering Paths

The web UI has **two independent markdown rendering paths**:

1. **smd (streaming)** — `ChatMessage.tsx` → `markdownRenderer.ts`
2. **marked** — `parseMarkdownContent()` in `markdownUtils.ts` (workspace previews, etc.)

If you add preprocessing (e.g. code block transformation), apply it in **both** paths. See inline comments in `ChatMessage.tsx` where `processNestedCodeBlocks` is called.

## Code Block Nesting Convention

gptme uses a convention where `` ```lang `` is always an **opener** and bare `` ``` `` is always a **closer**, allowing nesting. Neither parser understands this — `processNestedCodeBlocks()` widens outer fences before parsing. See its docstring in `markdownUtils.ts`.

## Legend State + React

Key gotcha: **`<For>` only re-renders on observable changes.** React `useState` is invisible inside `<For>` callbacks — use `useObservable`. See the comment at `ConversationContent.tsx` where `expandedGroups$` is declared.

## Server ↔ Web UI Data Flow

- **GET**: `LogManager.to_dict()` → `Message.to_dict()` (includes metadata)
- **SSE**: `msg2dict()` in `api_v2_common.py` (must also include metadata)
- **Streaming**: `generation_complete` sends final message with metadata; `onMessageComplete` in `useConversation.ts` must update metadata/timestamp from the event

## Step Grouping

`buildStepRoles()` in `stepGrouping.ts` collapses intermediate tool-use messages. The algorithm and edge cases are documented in inline comments and tests (`stepGrouping.test.ts`). Key design decisions:

- Response = last assistant message NOT followed by a tool result (backward search)
- Group IDs = message index of first step (stable across recomputations)
- Step count = system messages (tool results), not raw message count

## ChatInput State

`ChatInput` stays mounted across conversation switches — `useState` initializers don't re-run. Draft persistence uses `localStorage` keyed by `gptme-draft-{conversationId}`. See inline comments in `ChatInput.tsx` for the conversation-switch sync logic.
