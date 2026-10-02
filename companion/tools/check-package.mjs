// Checks a packaged app: every relative require() in the app's own code must point at a file that made it
// into app.asar (the installer once left out dist/shared). Run after `electron-builder --dir`:
//   node tools/check-package.mjs release/linux-unpacked/resources/app.asar
import asar from "@electron/asar";
import { posix } from "node:path";

const archive = process.argv[2] ?? "release/win-unpacked/resources/app.asar";
const files = new Set(asar.listPackage(archive).map((f) => f.replaceAll("\\", "/")));
const pkg = JSON.parse(asar.extractFile(archive, "package.json").toString("utf8"));
const missing = [];
const resolves = (p) => [p, `${p}.js`, `${p}/index.js`].some((c) => files.has(c));

if (!resolves(`/${pkg.main}`)) missing.push(`package.json main -> ${pkg.main}`);
for (const f of files) {
  if (!f.startsWith("/dist/") || !f.endsWith(".js")) continue;
  const code = asar.extractFile(archive, f.slice(1)).toString("utf8");
  for (const [, rel] of code.matchAll(/require\("(\.{1,2}\/[^"]+)"\)/g)) {
    const target = posix.join(posix.dirname(f), rel);
    if (!resolves(target)) missing.push(`${f} -> ${rel}`);
  }
}
if (missing.length) {
  console.error(`Missing from ${archive}:\n  ${missing.join("\n  ")}`);
  process.exit(1);
}
console.log(`${archive}: all requires resolve`);
