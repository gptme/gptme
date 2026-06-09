describe("Smoke test", () => {
  it("webview document reaches ready state", async () => {
    await browser.waitUntil(
      async () => (await browser.execute(() => document.readyState)) === "complete",
      {
        timeout: 30000,
        timeoutMsg: "Expected webview document to reach readyState=complete within 30s",
      }
    );

    const readyState = await browser.execute(() => document.readyState);
    console.log(`Document readyState: ${readyState}`);
    expect(readyState).toBe("complete");
  });

  it("webview body is present", async () => {
    const body = await $("body");
    await expect(body).toExist();
  });
});
