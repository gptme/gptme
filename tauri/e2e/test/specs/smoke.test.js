describe("Smoke test", () => {
  it("app window opens and webview loads", async () => {
    // Wait for the webview to have a title (indicates page loaded)
    await browser.waitUntil(
      async () => {
        const title = await browser.getTitle();
        return typeof title === "string" && title.length > 0;
      },
      {
        timeout: 30000,
        timeoutMsg: "Expected window title to be present within 30s",
      }
    );

    const title = await browser.getTitle();
    console.log(`App title: ${title}`);
    expect(title).toBeTruthy();
  });

  it("webview body is present", async () => {
    const body = await $("body");
    await expect(body).toExist();
  });
});
