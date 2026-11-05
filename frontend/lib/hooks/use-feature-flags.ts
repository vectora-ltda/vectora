"use client";

import { useEffect, useState } from "react";

interface FeatureFlags {
  enableFeaturesBeta: boolean;
}

const DEFAULT_FLAGS: FeatureFlags = {
  enableFeaturesBeta: false,
};

let flagsCache: FeatureFlags | null = null;
let flagsRequest: Promise<FeatureFlags> | null = null;

async function loadFlags(): Promise<FeatureFlags> {
  if (flagsCache) return flagsCache;
  if (flagsRequest) return flagsRequest;
  flagsRequest = fetch("/settings/flags")
    .then((r) =>
      r.ok
        ? (r.json() as Promise<{ enable_features_beta: boolean }>)
        : Promise.reject(),
    )
    .then((data) => {
      flagsCache = { enableFeaturesBeta: data.enable_features_beta };
      return flagsCache;
    })
    .catch(() => {
      flagsCache = DEFAULT_FLAGS;
      return DEFAULT_FLAGS;
    });
  return flagsRequest;
}

export function useFeatureFlags(): FeatureFlags {
  const [flags, setFlags] = useState<FeatureFlags>(flagsCache ?? DEFAULT_FLAGS);

  useEffect(() => {
    void loadFlags().then(setFlags);
  }, []);

  return flags;
}
