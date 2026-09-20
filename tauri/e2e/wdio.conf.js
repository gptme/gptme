const net = require("net");
const { spawn } = require("child_process");
const { resolve, join } = require("path");
const { homedir } = require("os");
const { rmSync, existsSync } = require("fs");

let tauriDriver;
const DRIVER_EXIT_TIMEOUT_MS = 5000;

async function reserveSidecarPort() {
  return new Promise((resolvePort, rejectPort) => {
    const server = net.createServer();
    let settled = false;
    const rejectOnce = (error) => {
      if (settled) return;
      settled = true;
      rejectPort(error);
    };

    server.once("error", rejectOnce);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        rejectOnce(new Error("Could not allocate an E2E sidecar port"));
        return;
      }
      server.close((error) => {
        if (error) {
          rejectOnce(error);
          return;
        }
        if (settled) return;
        settled = true;
        resolvePort(address.port);
      });
    });
  });
}

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

async function stopDriver(driverProcess, timeoutMs = DRIVER_EXIT_TIMEOUT_MS) {
  if (!driverProcess || driverProcess.exitCode !== null || driverProcess.signalCode !== null) {
    return;
  }

  await new Promise((resolveExit, rejectExit) => {
    const timeout = setTimeout(() => {
      rejectExit(new Error(`tauri-driver did not exit within ${timeoutMs}ms`));
    }, timeoutMs);
    driverProcess.once("exit", () => {
      clearTimeout(timeout);
      resolveExit();
    });
    driverProcess.kill();
  });
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

  beforeSession: async () => {
    // Clear the Tauri WebKit user-data directory so each test starts with a
    // clean localStorage / hasCompletedSetup=false.
    const candidates = [
      join(homedir(), ".local", "share", "org.gptme.tauri"),
      join(homedir(), ".local", "share", "gptme-tauri"),
      join(homedir(), ".config", "org.gptme.tauri"),
      join(homedir(), ".config", "gptme-tauri"),
    ];
    for (const dir of candidates) {
      if (existsSync(dir)) {
        console.log(`[wdio] Clearing Tauri profile: ${dir}`);
        rmSync(dir, { recursive: true, force: true });
      }
    }
  },

  onPrepare: async () => {
    // Reserve the sidecar port before tauri-driver starts. The driver launches
    // each Tauri app process, so assigning the variable in beforeSession is too
    // late: only the worker/test would see it, while the app would use 5700.
    const sidecarPort = await reserveSidecarPort();
    process.env.GPTME_SERVER_PORT = String(sidecarPort);
    console.log(`[wdio] Reserved sidecar port ${sidecarPort}`);

    // Launch tauri-driver alongside tests and wait for it to accept sessions.
    tauriDriver = spawn("tauri-driver", [], {
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env },
    });
    tauriDriver.stdout.pipe(process.stdout);
    tauriDriver.stderr.pipe(process.stderr);

    await waitForDriverReady(tauriDriver, 4444);
  },

  onComplete: async () => {
    // Shut down tauri-driver and wait for the launcher process itself. Each CI
    // spec uses a new sidecar port, so any PyInstaller child still unwinding
    // cannot be mistaken for the next app's managed server.
    await stopDriver(tauriDriver);
  },
};
