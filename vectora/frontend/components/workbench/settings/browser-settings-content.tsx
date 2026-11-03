"use client";

import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";
import { BrowserSettingsForm } from "./browser-settings-form";

/** Descriptor entry point for browser settings in the global workbench page. */
export function BrowserSettingsContent(context: WorkbenchSettingsContext) {
  return <BrowserSettingsForm {...context} />;
}
