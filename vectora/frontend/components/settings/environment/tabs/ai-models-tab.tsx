"use client";

import { MediaModelsSection } from "./provider-routing-tab";
import { m } from "@/lib/paraglide/messages";

/**
 * Dedicated AI configuration surface. Provider Routing remains focused on
 * transport/provider endpoints; capability defaults such as vision and audio
 * live here with the decision-model controls that will follow in this area.
 */
export function AiModelsTab() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-medium text-foreground">
          {m.settings_category_ai_models()}
        </h3>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          {m.ai_models_description()}
        </p>
      </div>
      <MediaModelsSection />
    </div>
  );
}
