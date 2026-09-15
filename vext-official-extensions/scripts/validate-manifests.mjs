import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";

const packagesDir = new URL("../packages/", import.meta.url);
const packageNames = await readdir(packagesDir);
for (const packageName of packageNames) {
  const manifestPath = new URL(`../packages/${packageName}/vectora-extension.json`, import.meta.url);
  const readmePath = new URL(`../packages/${packageName}/README.md`, import.meta.url);
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const readme = await readFile(readmePath, "utf8");
  if (!readme.trim()) throw new Error(`${packageName}: README.md is required`);
  for (const field of ["id", "name", "version", "frontend", "backend"]) {
    if (typeof manifest[field] !== "string" || !manifest[field]) {
      throw new Error(`${packageName}: missing manifest field ${field}`);
    }
  }
  if (manifest.api_version !== 1 || !Array.isArray(manifest.permissions)) {
    throw new Error(`${packageName}: invalid API version or permissions`);
  }
  if (manifest.frontend === manifest.backend) {
    throw new Error(`${packageName}: frontend and backend must be separate entrypoints`);
  }
}
console.log(`Validated ${packageNames.length} official VEXT manifests`);
