const net = require("net");
const { spawn } = require("child_process");
const { resolve } = require("path");

let tauriDriver;

async function waitForDriverReady(driverProcess, port, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    if (driverProcess.exitCode !== null || driverProcess.signalCode !== null) {
      throw new Error(
        `tauri-driver exited before becoming ready (exitCode=${driverProcess.exitCode}, signal=${driverProcess.signalCode})`
      );
    }

    try {
      await new Promise((resolveReady, rejectReady) => {
        const socket = net.createConnection({ host: "127.0.0.1", port });

        socket.once("connect", () => {
          socket.end();
          resolveReady();
        });
        socket.once("error", (error) => {
          socket.destroy();
          rejectReady(error);
        });
      });
      return;
    } catch (_error) {
      await new Promise((resolveRetry) => setTimeout(resolveRetry, 250));
    }
  }

  throw new Error(
    `tauri-driver did not start listening on port ${port} within ${timeoutMs}ms`
  );
}

exports.config = {
  specs: ["./test/specs/**/*.js"],
  maxInstances: 1,
  capabilities: [
    {
      maxInstances: 1,
      "tauri:options": {
        application: resolve(
          __dirname,
          "../src-tauri/target/debug/gptme-tauri"
        ),
      },
    },
  ],
  reporters: ["spec"],
  framework: "mocha",
  mochaOpts: {
    ui: "bdd",
    timeout: 60000,
  },
  hostname: "localhost",
  port: 4444,
  path: "/",

  onPrepare: async () => {
    // Launch tauri-driver alongside tests and wait for it to accept sessions.
    tauriDriver = spawn("tauri-driver", [], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    tauriDriver.stdout.pipe(process.stdout);
    tauriDriver.stderr.pipe(process.stderr);

    await waitForDriverReady(tauriDriver, 4444);
  },

  onComplete: () => {
    // Shut down tauri-driver when tests finish
    if (tauriDriver) {
      tauriDriver.kill();
    }
  },
};
