# Web UI Agent Guide

Architecture notes and gotchas for agents working on the gptme web UI.

## Rendering Paths

The web UI has **two independent markdown rendering paths**. Both must handle gptme-specific conventions (nested code blocks, thinking tags, etc.):

1. **smd (streaming)** — used by `ChatMessage.tsx` for chat messages. Character-by-character streaming parser. Custom renderer in `markdownRenderer.ts`.
2. **marked** — used by `parseMarkdownContent()` in `markdownUtils.ts` for non-chat rendering (workspace previews, etc.).

If you add preprocessing (e.g. code block transformation), apply it in **both** paths.

## Code Block Nesting Convention

gptme uses a convention where `` ```lang `` is always a code block **opener** and bare `` ``` `` is always a **closer**. This allows nesting (e.g. a `save` block containing a `python` block). Neither `marked` nor `smd` understand this — `processNestedCodeBlocks()` in `markdownUtils.ts` widens outer fences before parsing.

## Legend State + React

The UI uses [Legend State](https://legendapp.com/open-source/state/) for reactive state. Key gotcha:

- **`<For>` only re-renders on observable changes.** React `useState` changes are invisible inside `<For>` callbacks. Use `useObservable` for any state read inside `<For each={...}>`.
- Use `.get()` to read observables reactively, `.peek()` to read without subscribing.
- `useObserveEffect` runs when any `.get()` call inside it changes.

## Server ↔ Web UI Data Flow

- **Conversation loading** (GET): `LogManager.to_dict()` → `Message.to_dict()` (includes metadata)
- **Live events** (SSE): `msg2dict()` in `api_v2_common.py` (must also include metadata)
- **Streaming**: `generation_progress` sends tokens, `generation_complete` sends final message with metadata
- **`onMessageComplete`** in `useConversation.ts` must update metadata/timestamp from the event, not just content

## Message Chain Styling

Messages chain visually (connected borders, no rounding between them) based on `useMessageChainType`. Assistant messages are **borderless** — system messages following them must not get `border-t-0` or they lose their top border. The `visualChain` logic in `ChatMessage.tsx` handles this by checking both neighbors.

## Step Grouping

`buildStepRoles()` in `stepGrouping.ts` collapses intermediate tool-use messages between user messages. Key rules:

- **Response**: always the last assistant message in the turn (backward search), even if system messages follow it
- **Group ID**: uses the message index of the first step (stable across recomputations)
- **Step count**: counts system messages (tool results), not raw message count
- **Threshold**: ≥2 intermediate steps to collapse

## ChatInput State

`ChatInput` can be **controlled** (WelcomeView passes `value`/`onChange`) or **uncontrolled** (ConversationContent lets it manage its own `internalMessage`). Draft persistence uses `localStorage` keyed by `gptme-draft-{conversationId}` or `gptme-draft-new`.

The component **stays mounted** across conversation switches — `useState` initializers don't re-run. State that depends on `conversationId` needs explicit sync via `useEffect`.
