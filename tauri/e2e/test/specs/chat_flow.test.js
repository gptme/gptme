/**
 * Chat interaction flow — Slice 2 E2E tests.
 *
 * These tests run against the chat-mock.html fixture (configured in CI via
 * the "Create webui placeholder" step in tauri.yml, or locally by pointing
 * the built app at tauri/e2e/fixtures/chat-mock.html).
 *
 * Covers:
 * - Chat input and send button are present
 * - Sending a message adds it to the message list
 * - An assistant response appears after the user message
 * - The conversation title updates from the first user message
 */
describe("Chat interaction flow", () => {
  before(async () => {
    await browser.waitUntil(
      async () => {
        try {
          const app = await $("#app");
          const ready = await app.getAttribute("data-ready");
          return ready === "true";
        } catch (_e) {
          return false;
        }
      },
      {
        timeout: 30000,
        timeoutMsg: "App did not reach ready state (data-ready=true) within 30s",
      }
    );
  });

  it("chat input element is present and editable", async () => {
    const input = await $('[data-testid="message-input"]');
    await expect(input).toExist();
    await expect(input).toBeEnabled();
  });

  it("send button is present", async () => {
    const btn = await $('[data-testid="send-button"]');
    await expect(btn).toExist();
    await expect(btn).toBeEnabled();
  });

  it("message list container is present", async () => {
    const list = await $('[data-testid="message-list"]');
    await expect(list).toExist();
  });

  it("sending a message appends it to the chat as a user message", async () => {
    const input = await $('[data-testid="message-input"]');
    await input.setValue("Hello, gptme!");

    const sendBtn = await $('[data-testid="send-button"]');
    await sendBtn.click();

    const userMsg = await $('[data-testid="message-user-0"]');
    await expect(userMsg).toExist();
    await expect(userMsg).toHaveText("Hello, gptme!");
  });

  it("input field is cleared after sending", async () => {
    const input = await $('[data-testid="message-input"]');
    const value = await input.getValue();
    expect(value).toBe("");
  });

  it("assistant response appears after user message", async () => {
    await browser.waitUntil(
      async () => {
        const responses = await $$('[data-testid^="message-assistant-"]');
        return responses.length > 0;
      },
      {
        timeout: 10000,
        timeoutMsg: "No assistant response appeared within 10s",
      }
    );

    const response = await $('[data-testid^="message-assistant-"]');
    await expect(response).toExist();
    const text = await response.getText();
    expect(text.length).toBeGreaterThan(0);
  });

  it("conversation title updates from the first user message", async () => {
    const title = await $('[data-testid="conversation-title"]');
    await expect(title).toExist();
    const text = await title.getText();
    expect(text).not.toBe("New Conversation");
    expect(text.length).toBeGreaterThan(0);
  });

  it("Enter key sends a message", async () => {
    const input = await $('[data-testid="message-input"]');
    await input.setValue("Second message via Enter");
    await browser.keys("Enter");

    await browser.waitUntil(
      async () => {
        const msgs = await $$('[data-testid^="message-user-"]');
        return msgs.length >= 2;
      },
      {
        timeout: 5000,
        timeoutMsg: "Second user message did not appear after pressing Enter",
      }
    );

    const msgs = await $$('[data-testid^="message-user-"]');
    expect(msgs.length).toBeGreaterThanOrEqual(2);
  });
});
