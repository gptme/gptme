// Copy shared brand assets from the repo's media/ into public/media/ so the
// site always uses the same logo files as the README and docs.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoMedia = join(here, "..", "..", "..", "media");
const out = join(here, "..", "public", "media");

mkdirSync(out, { recursive: true });
for (const file of ["logo.png", "icon.svg"]) {
  copyFileSync(join(repoMedia, file), join(out, file));
}
console.log(`copied ${repoMedia}/{logo.png,icon.svg} -> public/media/`);
