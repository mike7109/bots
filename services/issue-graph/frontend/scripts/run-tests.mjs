// Прогон фронтовых юнит-тестов без vitest (его в проекте нет): бандлим тест
// esbuild'ом (esbuild уже стоит как зависимость vite) и запускаем в node.
// Так покрываем чистые функции раскладки (Этап 8, Kanban) без сети и без DOM.
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");

const tests = ["src/__tests__/layout.kanban.test.ts"];

const outdir = mkdtempSync(resolve(tmpdir(), "ig-fe-tests-"));
let failed = false;

for (const t of tests) {
  const out = resolve(outdir, t.replace(/[\\/]/g, "_").replace(/\.ts$/, ".mjs"));
  await build({
    entryPoints: [resolve(root, t)],
    bundle: true,
    platform: "node",
    format: "esm",
    outfile: out,
    logLevel: "warning",
  });
  const r = spawnSync(process.execPath, [out], { stdio: "inherit" });
  if (r.status !== 0) failed = true;
}

process.exit(failed ? 1 : 0);
