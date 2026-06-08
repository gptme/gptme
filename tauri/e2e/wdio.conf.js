const { spawn } = require("child_process");
const { resolve } = require("path");

let tauriDriver;

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

  onPrepare: () => {
    // Launch tauri-driver alongside tests
    tauriDriver = spawn("tauri-driver", [], {
      stdio: [null, process.stdout, process.stderr],
    });
  },

  onComplete: () => {
    // Shut down tauri-driver when tests finish
    if (tauriDriver) {
      tauriDriver.kill();
    }
  },
};
