"use client";

/**
 * SkillsSection — gerencia skills instaladas (backend/workspace/skills.py,
 * GET/POST /skills, DELETE /skills/:id, POST /skills/:id/verify).
 * Reaproveita o componente SkillsTab sem duplicar o fetch das skills locais.
 *
 * Abaixo dela, "Catálogo" lista skills curadas do registry remoto
 * (GET /skills/catalog, distinto de GET /skills que lista as instaladas) —
 * instalar uma reaproveita POST /skills {source}. Não existe entrada manual:
 * toda instalação começa em um item publicado no catálogo.
 */

import { useEffect, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Download,
  Loader2,
  Sparkles,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { SkillsTab } from "@/components/settings/environment/tabs/skills-tab";
import { m } from "@/lib/paraglide/messages";
import {
  skillTrustLevel,
  useLibraryStore,
  type CatalogSkill,
} from "@/lib/stores/library-store";
import { LibraryCard, LibraryTag } from "./library-card";

const TRUST_LABEL = {
  builtin: m.library_skills_trust_builtin,
  verified: m.library_skills_trust_verified,
  community: m.library_skills_trust_community,
} as const;

const TRUST_STATE_LABEL = {
  vectora_verified: m.library_skills_trust_builtin,
  publisher_signed: m.library_skills_trust_publisher_signed,
  community_listed: m.library_skills_trust_community_listed,
  unsigned: m.library_skills_trust_unsigned,
  invalid: m.library_skills_trust_invalid,
  verification_unavailable: m.library_skills_trust_verification_unavailable,
} as const;

function CatalogCard({ skill }: { skill: CatalogSkill }) {
  const [busy, setBusy] = useState(false);
  const [installed, setInstalled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const trustState =
    skill.trust_state ??
    (skillTrustLevel(skill) === "builtin"
      ? "vectora_verified"
      : skillTrustLevel(skill) === "verified"
        ? "publisher_signed"
        : "community_listed");
  const invalid = trustState === "invalid";
  const requiresConfirmation = [
    "community_listed",
    "unsigned",
    "verification_unavailable",
  ].includes(trustState);
  const legacyTrust = skillTrustLevel(skill);
  const badgeLabel = skill.trust_state
    ? TRUST_STATE_LABEL[skill.trust_state]()
    : TRUST_LABEL[legacyTrust]();
  const showTrustBadge = trustState !== "community_listed";

  const handleInstall = async () => {
    if (invalid) {
      setError(m.library_skills_trust_invalid_install());
      return;
    }
    if (
      requiresConfirmation &&
      !window.confirm(m.library_skills_trust_confirm())
    )
      return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: skill.source,
          confirm_unverified: requiresConfirmation,
        }),
      });
      if (!res.ok) {
        setError(m.library_skills_catalog_error_install());
        return;
      }
      setInstalled(true);
    } catch {
      setError(m.library_skills_catalog_error_install());
    } finally {
      setBusy(false);
    }
  };

  return (
    <LibraryCard
      icon={<Sparkles className="size-3.5" />}
      title={skill.name}
      description={skill.description}
      tags={
        showTrustBadge ? (
          <LibraryTag
            verified={legacyTrust !== "community"}
            aria-label={`${m.library_skills_trust_aria_prefix()}: ${badgeLabel}`}
          >
            {badgeLabel}
          </LibraryTag>
        ) : undefined
      }
      action={
        <Button
          variant={installed ? "outline" : "default"}
          size="sm"
          className={
            installed
              ? "h-[19px] rounded-md border-[#555] bg-transparent px-1.5 py-1 text-[9px] text-muted-foreground"
              : "h-[19px] rounded-md border-0 bg-[#d4d4d4] px-1.5 py-1 text-[9px] font-medium text-[#1a1a1a] hover:bg-white"
          }
          onClick={handleInstall}
          disabled={busy || installed || invalid}
        >
          {busy ? (
            <Loader2 className="size-[11px] animate-spin" />
          ) : (
            <>
              <Download className="size-[11px]" />
              {m.library_skills_catalog_install()}
            </>
          )}
        </Button>
      }
      footer={
        <>
          {skill.trust_reason && (
            <p className="text-xs text-muted-foreground" role="status">
              {skill.trust_reason}
            </p>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </>
      }
    />
  );
}

function SkillsCatalog({ query }: { query: string }) {
  const [open, setOpen] = useState(true);
  const entries = useLibraryStore((s) => s.skillsItems);
  const loading = useLibraryStore((s) => s.skillsLoading);
  const error = useLibraryStore((s) => s.skillsError);
  const ensureSkillsLoaded = useLibraryStore((s) => s.ensureSkillsLoaded);

  useEffect(() => {
    if (!open) return;
    if (!query.trim()) {
      void ensureSkillsLoaded(query);
      return;
    }
    const timer = setTimeout(() => {
      void ensureSkillsLoaded(query);
    }, 350);
    return () => clearTimeout(timer);
  }, [open, query, ensureSkillsLoaded]);

  const content = loading ? (
    <div className="flex justify-center py-4">
      <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
    </div>
  ) : entries.length === 0 ? (
    <div className="py-2 space-y-1">
      <p className="text-xs text-muted-foreground text-center">
        {m.library_skills_catalog_empty()}
      </p>
      {error && <p className="text-xs text-destructive text-center">{error}</p>}
    </div>
  ) : (
    <div className="space-y-2 py-1">
      {error && <p className="text-xs text-destructive">{error}</p>}
      {entries.map((skill) => (
        <CatalogCard key={skill.id} skill={skill} />
      ))}
    </div>
  );

  return (
    <div className="pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        {open ? (
          <ChevronUp className="w-3.5 h-3.5" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5" />
        )}
        {m.library_skills_catalog_toggle()}
      </button>
      {open && content}
    </div>
  );
}

export function SkillsSection({ query }: { query: string }) {
  return (
    <div className="space-y-1">
      <SkillsTab />
      <SkillsCatalog query={query} />
    </div>
  );
}
