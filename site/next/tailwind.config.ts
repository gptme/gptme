import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";
import plugin from "tailwindcss/plugin";
import gptmePreset from "./tailwind.preset";

export default {
  presets: [gptmePreset],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    // Breakpoints from the design: mobile board <=600px, stacked layout <=900px,
    // tighter desktop <=1100px. Components are written desktop-first with
    // max-sm / max-md / max-lg variants.
    screens: {
      sm: "601px",
      md: "901px",
      lg: "1101px",
      xl: "1441px",
    },
  },
  plugins: [
    typography,
    // `js:` applies once the inline head script has added class="js" to <html>;
    // `no-js:` is the fallback (e.g. nav links wrap instead of a menu button).
    plugin(({ addVariant }) => {
      addVariant("js", ".js &");
      addVariant("no-js", "html:not(.js) &");
    }),
  ],
} satisfies Config;
