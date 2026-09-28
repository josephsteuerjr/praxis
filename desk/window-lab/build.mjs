// Сборка лаборатории в dist/: один lab.js (IIFE — работает и с file://, и в Tauri, и в
// WebKitGTK), стили и шрифты рядом. dist самодостаточен — его грузят все четыре движка.
import { build } from "esbuild";
import { cpSync, mkdirSync, rmSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dist = join(here, "dist");
rmSync(dist, { recursive: true, force: true });
mkdirSync(join(dist, "fonts"), { recursive: true });

await build({
  entryPoints: [join(here, "src", "lab.ts")],
  bundle: true,
  format: "iife",
  target: ["chrome110", "safari16"],
  outfile: join(dist, "lab.js"),
  minify: false,
  sourcemap: false,
  logLevel: "info",
});

for (const f of ["index.html", "lab.css"]) cpSync(join(here, "src", f), join(dist, f));
cpSync(join(here, "candidates.css"), join(dist, "candidates.css"));
cpSync(join(here, "fonts"), join(dist, "fonts"), { recursive: true });
const kit = join(here, "..", "ui-kit", "fonts");
for (const f of readdirSync(kit)) cpSync(join(kit, f), join(dist, "fonts", f));
console.log("dist готов:", dist);
