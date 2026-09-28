/** Resolve the isolated profile used by one Vectora runtime. */

import * as path from "path";

export interface RuntimeProfileEnv {
  VECTORA_RUNTIME_PROFILE?: string;
  VECTORA_HOME?: string;
}

export function resolveRuntimeProfile(
  env: RuntimeProfileEnv,
  isPackaged: boolean,
): string {
  const raw = env.VECTORA_RUNTIME_PROFILE ?? (isPackaged ? "stable" : "dev");
  return raw.replace(/[^A-Za-z0-9_-]/g, "-");
}

export function runtimeUserDataName(profile: string): string {
  return profile === "stable" ? "vectora" : `vectora-${profile}`;
}

export function runtimeHome(
  env: RuntimeProfileEnv,
  profile: string,
  homeDirectory: string,
): string {
  return (
    env.VECTORA_HOME ??
    path.join(homeDirectory, profile === "stable" ? ".vectora" : ".vectora-dev")
  );
}
