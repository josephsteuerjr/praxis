// Сборка оболочки: main и preload — по одному CommonJS-файлу в out/ (electron — внешний).
import { build } from "esbuild";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const common = { bundle: true, platform: "node", format: "cjs", target: "node22", external: ["electron"], logLevel: "info", sourcemap: false };
await build({ ...common, entryPoints: [join(here, "src", "main.ts")], outfile: join(here, "out", "main.js") });
await build({ ...common, entryPoints: [join(here, "src", "preload.ts")], outfile: join(here, "out", "preload.js") });
