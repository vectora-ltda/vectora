import { useState, useEffect } from "react";
import { Link } from "@tanstack/react-router";
import { m } from "#/paraglide/messages";

declare global {
  interface Window {
    gtag?: (...args: unknown[]) => void;
  }
}

const STORAGE_KEY = "cookie-consent";

export default function CookieConsent() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // localStorage não existe no SSR — leitura de sistema externo, só
    // pode rodar no cliente.
    // oxlint-disable-next-line react/set-state-in-effect
    if (!localStorage.getItem(STORAGE_KEY)) setVisible(true);
  }, []);

  const updateGtag = (granted: boolean) => {
    window.gtag?.("consent", "update", {
      analytics_storage: granted ? "granted" : "denied",
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
    });
  };

  const accept = () => {
    localStorage.setItem(STORAGE_KEY, "accepted");
    setVisible(false);
    updateGtag(true);
  };

  const reject = () => {
    localStorage.setItem(STORAGE_KEY, "rejected");
    setVisible(false);
    updateGtag(false);
  };

  if (!visible) return null;

  return (
    <div
      role="dialog"
      aria-label={m.cookie_title()}
      className="fixed bottom-0 start-0 end-0 z-50 border-t border-border bg-card/95 backdrop-blur-sm"
    >
      <div className="mx-auto flex max-w-[1024px] flex-col gap-4 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:gap-8 sm:px-6">
        <p className="text-sm text-muted-foreground">
          <span className="font-semibold text-foreground">
            {m.cookie_title()}.{" "}
          </span>
          {m.cookie_desc()}{" "}
          <Link
            to="/privacy"
            className="underline underline-offset-2 transition-colors hover:text-foreground"
          >
            {m.cookie_privacy_link()}
          </Link>
          {" · "}
          <Link
            to="/privacy"
            hash="cookies-e-analytics"
            className="underline underline-offset-2 transition-colors hover:text-foreground"
          >
            {m.cookie_policy_link()}
          </Link>
        </p>

        <div className="flex shrink-0 gap-2">
          <button
            onClick={reject}
            className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground"
          >
            {m.cookie_reject()}
          </button>
          <button
            onClick={accept}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow shadow-primary/25 transition-all hover:bg-primary/90"
          >
            {m.cookie_accept()}
          </button>
        </div>
      </div>
    </div>
  );
}
