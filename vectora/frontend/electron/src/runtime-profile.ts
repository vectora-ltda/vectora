/** Resolve the isolated profile used by one Vectora runtime. */

import * as path from "path";

export interface RuntimeProfileEnv {
  VECTORA_RUNTIME_PROFILE?: string;
  VECTORA_HOME?: string;
}

/** Resolve the effective profile while preserving the packaged default. */
export function resolveRuntimeProfile(
  env: RuntimeProfileEnv,
  isPackaged: boolean,
): string {
  const raw = env.VECTORA_RUNTIME_PROFILE ?? (isPackaged ? "stable" : "dev");
  return raw.replace(/[^A-Za-z0-9_-]/g, "-");
}

/** Return the Electron userData directory name for a runtime profile. */
export function runtimeUserDataName(profile: string): string {
  return profile === "stable" ? "vectora" : `vectora-${profile}`;
}

/** Return the backend home, keeping explicit overrides authoritative. */
export function runtimeHome(
  env: RuntimeProfileEnv,
  profile: string,
  homeDirectory: string,
): string {
  if (env.VECTORA_HOME) return env.VECTORA_HOME;
  const directoryName =
    profile === "stable"
      ? ".vectora"
      : profile === "dev"
        ? ".vectora-dev"
        : `.vectora-${profile}`;
  return path.join(homeDirectory, directoryName);
}
