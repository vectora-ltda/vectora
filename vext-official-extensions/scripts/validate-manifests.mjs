import { access, readdir, readFile } from "node:fs/promises";
import { join } from "node:path";

const packagesDir = new URL("../packages/", import.meta.url);
const packageNames = await readdir(packagesDir);
for (const packageName of packageNames) {
  const packageDir = new URL(`../packages/${packageName}/`, import.meta.url);
  const manifestPath = new URL(`../packages/${packageName}/vectora-extension.json`, import.meta.url);
  const readmePath = new URL(`../packages/${packageName}/README.md`, import.meta.url);
  const packageJsonPath = new URL(`../packages/${packageName}/package.json`, import.meta.url);
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const packageJson = JSON.parse(await readFile(packageJsonPath, "utf8"));
  const publishFiles = Array.isArray(packageJson.files) ? packageJson.files : [];
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
  for (const field of ["icon", "frontend", "backend"]) {
    const relativePath = manifest[field];
    if (relativePath.includes("..") || relativePath.startsWith("/")) {
      throw new Error(`${packageName}: ${field} must stay inside the package`);
    }
    try {
      await access(new URL(relativePath, packageDir));
    } catch {
      throw new Error(`${packageName}: ${field} is missing from the publishable package`);
    }
    const topLevel = relativePath.split("/")[0];
    if (!publishFiles.includes(topLevel)) {
      throw new Error(`${packageName}: ${field} is not included in package.json files`);
    }
  }
}
console.log(`Validated ${packageNames.length} official VEXT manifests`);
