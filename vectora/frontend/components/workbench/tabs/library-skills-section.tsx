"use client";

/**
 * SkillsSection — gerencia skills instaladas (backend/workspace/skills.py,
 * GET/POST /skills, DELETE /skills/:id, POST /skills/:id/verify).
 * Reaproveita o componente SkillsTab, usando seu callback onSkillsChange
 * pra manter o badge "(N)" do accordion em dia sem duplicar o fetch.
 *
 * Abaixo dela, "Catálogo" lista skills curadas do registry remoto
 * (GET /skills/catalog, distinto de GET /skills que lista as instaladas) —
 * instalar uma reaproveita POST /skills {source} (mesmo endpoint do form
 * manual do SkillsTab).
 */

import { useEffect, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Download,
  Loader2,
  Sparkles,
  Upload,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { SkillsTab } from "@/components/settings/environment/tabs/skills-tab";
import { useLicenseStatus } from "@/lib/hooks/use-license-status";
import { m } from "@/lib/paraglide/messages";
import {
  skillTrustLevel,
  useLibraryStore,
  type CatalogSkill,
} from "@/lib/stores/library-store";

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

async function publishSkill(payload: {
  source: string;
  name: string;
  description: string;
  category: string;
}): Promise<{ status: string; skill_id?: string; error?: string }> {
  const res = await fetch("/skills/publish", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

function PublishDialog({
  onClose,
  onPublished,
}: {
  onClose: () => void;
  onPublished: () => void;
}) {
  const [source, setSource] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = async () => {
    if (!source.trim() || !name.trim() || !description.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const result = await publishSkill({
        source: source.trim(),
        name: name.trim(),
        description: description.trim(),
        category: category.trim(),
      });
      if (result.status === "error") {
        setError(result.error ?? m.library_skills_error_publish());
        return;
      }
      onPublished();
      onClose();
    } catch {
      setError(m.library_skills_error_publish());
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{m.library_skills_publish_title()}</DialogTitle>
          <DialogDescription>
            {m.library_skills_publish_desc()}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-1">
          <div className="space-y-1.5">
            <label
              htmlFor="publish-skill-source"
              className="text-xs font-medium text-muted-foreground"
            >
              {m.library_skills_publish_source()}
            </label>
            <Input
              id="publish-skill-source"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder={m.library_skills_publish_source_placeholder()}
              className="text-sm font-mono"
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <label
              htmlFor="publish-skill-name"
              className="text-xs font-medium text-muted-foreground"
            >
              {m.library_skills_publish_name()}
            </label>
            <Input
              id="publish-skill-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="text-sm"
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <label
              htmlFor="publish-skill-description"
              className="text-xs font-medium text-muted-foreground"
            >
              {m.library_skills_publish_description()}
            </label>
            <Textarea
              id="publish-skill-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="text-sm"
            />
          </div>
          <div className="space-y-1.5">
            <label
              htmlFor="publish-skill-category"
              className="text-xs font-medium text-muted-foreground"
            >
              {m.library_skills_publish_category()}
            </label>
            <Input
              id="publish-skill-category"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="text-sm"
              autoComplete="off"
            />
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            {m.envs_cancel()}
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={
              saving || !source.trim() || !name.trim() || !description.trim()
            }
          >
            {saving && <Loader2 className="w-4 h-4 animate-spin mr-2" />}
            {m.library_skills_publish_confirm()}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

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
  const badgeVariant = skill.trust_state
    ? trustState === "vectora_verified"
      ? "default"
      : trustState === "publisher_signed"
        ? "secondary"
        : "outline"
    : legacyTrust === "builtin"
      ? "default"
      : legacyTrust === "verified"
        ? "secondary"
        : "outline";

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
    <div className="rounded-lg border bg-card p-3 space-y-2">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-md bg-muted flex items-center justify-center shrink-0">
          <Sparkles className="w-4 h-4 text-muted-foreground" />
        </div>
        <div className="flex-1 min-w-0">
          <span className="text-sm font-medium truncate block">
            {skill.name}
          </span>
          <p className="text-xs text-muted-foreground truncate">
            {skill.description}
          </p>
          <div className="pt-0.5">
            <Badge
              variant={badgeVariant}
              className="text-[10px] h-4 px-1.5 shrink-0"
              aria-label={`${m.library_skills_trust_aria_prefix()}: ${badgeLabel}`}
            >
              {badgeLabel}
            </Badge>
          </div>
        </div>
        <Button
          variant={installed ? "outline" : "default"}
          size="sm"
          className="h-7 text-xs shrink-0"
          onClick={handleInstall}
          disabled={busy || installed || invalid}
        >
          {busy ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <>
              <Download className="w-3 h-3 mr-1.5" />
              {m.library_skills_catalog_install()}
            </>
          )}
        </Button>
      </div>
      {skill.trust_reason && (
        <p className="text-xs text-muted-foreground" role="status">
          {skill.trust_reason}
        </p>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
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

export function SkillsSection({
  query,
  onCountChange,
}: {
  query: string;
  onCountChange: (count: number) => void;
}) {
  const { status: licenseStatus } = useLicenseStatus();
  const invalidateSkills = useLibraryStore((s) => s.invalidateSkills);
  const ensureSkillsLoaded = useLibraryStore((s) => s.ensureSkillsLoaded);
  const [publishing, setPublishing] = useState(false);
  const canPublish = Boolean(licenseStatus?.configured);

  return (
    <div className="space-y-1">
      <SkillsTab onSkillsChange={onCountChange} />
      <SkillsCatalog query={query} />
      {canPublish ? (
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs w-full mt-2"
          onClick={() => setPublishing(true)}
        >
          <Upload className="w-3 h-3 mr-1.5" />
          {m.library_skills_publish_button()}
        </Button>
      ) : (
        <p className="text-[10px] text-muted-foreground/70 pt-1">
          {m.library_skills_publish_note()}
        </p>
      )}
      {publishing && (
        <PublishDialog
          onClose={() => setPublishing(false)}
          onPublished={() => {
            invalidateSkills();
            void ensureSkillsLoaded(query);
          }}
        />
      )}
    </div>
  );
}
