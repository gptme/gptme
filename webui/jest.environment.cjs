// jsdom test environment with a hook to change window.location.
//
// jsdom 26 (bundled with jest-environment-jsdom 30) makes `window.location`
// unforgeable: Object.defineProperty(window, 'location', ...) throws "Cannot
// redefine property: location". The supported way to change the URL is
// JSDOM#reconfigure, which only the environment can reach, so expose it to
// tests as a global. See src/test-utils/location.ts for the typed wrapper.
const jsdomEnvironment = require('jest-environment-jsdom');

const BaseEnvironment =
  jsdomEnvironment.TestEnvironment || jsdomEnvironment.default || jsdomEnvironment;

class ReconfigurableJsdomEnvironment extends BaseEnvironment {
  constructor(config, context) {
    super(config, context);
    this.global.__jsdomReconfigure = (url) => {
      this.dom.reconfigure({ url });
    };
  }
}

module.exports = ReconfigurableJsdomEnvironment;
