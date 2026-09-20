const net = require("net");
const { spawn } = require("child_process");
const { resolve, join } = require("path");
const { homedir } = require("os");
const { rmSync, existsSync } = require("fs");

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

  // Clean up between sessions:
  //
  // 1. Kill the orphaned gptme-server sidecar. When tauri-driver's deleteSession
  //    kills the Tauri binary, the sidecar (externalBin) is reparented to init
  //    and keeps running on GPTME_SERVER_PORT. A live sidecar causes the next
  //    session's ApiContext to see isConnected=true immediately, which triggers
  //    SetupWizard's auto-advance effect (checkProviderAndAdvance) before the
  //    test can interact with the welcome step.
  //
  // 2. Clear the Tauri WebKit user-data directory so each test starts with a
  //    clean localStorage / hasCompletedSetup=false.
  beforeSession: async () => {
    const { execSync } = require("child_process");
    const sidecarPort = Number(process.env.GPTME_SERVER_PORT || "5700");

    // Kill whatever process owns the sidecar port.
    // Use pkill (universally available) as primary; fuser as secondary for
    // the port-level kill (catches non-gptme processes on that port).
    try {
      execSync("pkill -9 -f gptme-server 2>/dev/null || true", {
        shell: true,
        stdio: "ignore",
      });
    } catch (_) {}
    try {
      execSync(`fuser -k -KILL ${sidecarPort}/tcp 2>/dev/null || true`, {
        shell: true,
        stdio: "ignore",
      });
    } catch (_) {
      // fuser not available — pkill above is sufficient
    }
    await new Promise((r) => setTimeout(r, 1000));

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
    // Kill any lingering sidecar that survived tauri-driver termination.
    const { spawnSync } = require("child_process");
    spawnSync("pkill", ["-9", "-f", "gptme-server"], { stdio: "ignore" });
  },
};
