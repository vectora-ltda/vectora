"use client";

import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";
import { BrowserSettingsForm } from "./browser-settings-form";

/** Shared Browser settings surface used by both the workbench and global Settings. */
export function BrowserSettingsContent(context: WorkbenchSettingsContext) {
  return <BrowserSettingsForm {...context} />;
}
