"use client";

import {
  RagSettingsForm,
  useRagSettings,
} from "@/components/workbench/rag-settings-panel";

export function MemorySettings() {
  const state = useRagSettings();
  return <RagSettingsForm {...state} />;
}
