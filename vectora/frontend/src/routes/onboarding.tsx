import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { PreAuthWizard } from "@/components/onboarding/pre-auth-wizard";

const searchSchema = z.object({
  /** "1" quando chega de volta de um /auth/signup (VPS) bem-sucedido —
   * pula identity/mode/vps-token direto pros passos compartilhados. */
  continue: z.literal("1").optional(),
});

export const Route = createFileRoute("/onboarding")({
  validateSearch: searchSchema,
  component: OnboardingRoute,
});

function OnboardingRoute() {
  const { continue: cont } = Route.useSearch();
  return (
    <div className="h-full min-h-0 overflow-y-auto">
      <PreAuthWizard startAtContinuation={cont === "1"} />
    </div>
  );
}
