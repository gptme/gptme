// Client entry. Pages are prerendered to static HTML, so there is no React
// hydration: this file only loads the styles and adds two small enhancements.
import "./styles/index.css";

// Mobile menu toggle. Without JS the nav links wrap under the brand instead.
const toggle = document.querySelector<HTMLButtonElement>("[data-nav-toggle]");
const menu = document.getElementById("nav-links");
if (toggle && menu) {
  const setOpen = (open: boolean) => {
    menu.dataset.open = String(open);
    toggle.setAttribute("aria-expanded", String(open));
  };
  toggle.addEventListener("click", () => setOpen(menu.dataset.open !== "true"));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menu.dataset.open === "true") {
      setOpen(false);
      toggle.focus();
    }
  });
}

// Copy-to-clipboard buttons, hidden until JS and the Clipboard API are available.
const status = document.getElementById("copy-status");
let copyTimer = 0;
let lastCopied: HTMLButtonElement | undefined;
document.querySelectorAll<HTMLButtonElement>("[data-copy]").forEach((btn) => {
  if (!navigator.clipboard) return;
  btn.hidden = false;
  btn.addEventListener("click", async () => {
    window.clearTimeout(copyTimer);
    if (lastCopied && lastCopied !== btn) delete lastCopied.dataset.state;
    lastCopied = btn;
    try {
      await navigator.clipboard.writeText(btn.dataset.copy ?? "");
      btn.dataset.state = "copied";
      if (status) status.textContent = "Copied to clipboard";
    } catch {
      delete btn.dataset.state;
      if (status) status.textContent = "Copy failed";
    }
    copyTimer = window.setTimeout(() => {
      delete btn.dataset.state;
      if (status) status.textContent = "";
      lastCopied = undefined;
    }, 1800);
  });
});
