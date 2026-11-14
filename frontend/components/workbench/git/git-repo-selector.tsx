"use client";

/**
 * Seletor do repositório que o painel Git mostra.
 *
 * Um workspace pode ser uma pasta com vários repositórios ou um repositório com
 * submódulos; a escolha é gravada no `vectora.toml` (`[git] repo`) pelo backend e
 * vale para todo o painel. Só aparece quando há o que escolher: com um único
 * repositório (a raiz) não ocupa espaço.
 */

import { GitFork } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { m } from "@/lib/paraglide/messages";
import { useToastStore } from "@/lib/stores/toast-store";
import {
  fetchGitRepos,
  selectGitRepo,
  type GitRepo,
  type GitRepos,
} from "./api";

/** Valor do item do repositório padrão: o Select não aceita string vazia. */
const DEFAULT_VALUE = "__default__";

function kindLabel(kind: GitRepo["kind"]): string {
  switch (kind) {
    case "root":
      return m.git_repo_kind_root();
    case "submodule":
      return m.git_repo_kind_submodule();
    case "worktree":
      return m.git_repo_kind_worktree();
    default:
      return "";
  }
}

function optionLabel(repo: GitRepo): string {
  const kind = kindLabel(repo.kind);
  const name = repo.path || repo.name;
  return kind ? `${name} · ${kind}` : name;
}

/** Só há o que escolher com mais de um repositório, ou sem repositório na raiz. */
function shouldShow(data: GitRepos): boolean {
  if (data.repos.length === 0) return false;
  if (data.repos.length === 1) {
    return data.repos[0]!.path !== "" || data.selected !== "";
  }
  return true;
}

export function GitRepoSelector({
  workspaceId,
  onSelected,
}: {
  workspaceId: string;
  /** Chamado depois que o usuário troca de repositório e o backend confirma. */
  onSelected?: (selected: string) => void;
}) {
  const [data, setData] = useState<GitRepos | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    void fetchGitRepos(workspaceId).then((result) => {
      if (active) setData(result);
    });
    return () => {
      active = false;
    };
  }, [workspaceId]);

  const handleChange = useCallback(
    async (value: string) => {
      const repo = value === DEFAULT_VALUE ? "" : value;
      setSaving(true);
      try {
        const updated = await selectGitRepo(workspaceId, repo);
        setData(updated);
        onSelected?.(updated.selected);
      } catch (error) {
        useToastStore.getState().error(m.git_repo_error(), {
          description: error instanceof Error ? error.message : undefined,
        });
      } finally {
        setSaving(false);
      }
    },
    [workspaceId, onSelected],
  );

  if (!data || !shouldShow(data)) return null;

  const hasDefault = data.repos.some((repo) => repo.path === "");
  const current = data.selected || (hasDefault ? DEFAULT_VALUE : undefined);

  const needsChoice = !hasDefault && data.selected === "";

  return (
    <div
      className="flex shrink-0 flex-col gap-1 border-b border-border/60 px-2 py-1.5"
      data-testid="git-repo-selector"
    >
      <div className="flex items-center gap-1.5">
        <GitFork className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <Select
          value={current}
          onValueChange={(value) => void handleChange(value)}
          disabled={saving}
        >
          <SelectTrigger
            className="h-7 min-w-0 flex-1 text-xs"
            aria-label={m.git_repo_label()}
          >
            <SelectValue placeholder={m.git_repo_placeholder()} />
          </SelectTrigger>
          <SelectContent>
            {data.repos.map((repo) => (
              <SelectItem
                key={repo.path || DEFAULT_VALUE}
                value={repo.path || DEFAULT_VALUE}
                className="text-xs"
              >
                {optionLabel(repo)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {needsChoice && (
        <p className="text-[11px] text-muted-foreground">
          {m.git_repo_choose_hint()}
        </p>
      )}
    </div>
  );
}
