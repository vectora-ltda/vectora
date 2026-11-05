"use client";

/**
 * MemoriaTab — lista e gerencia memórias persistentes do usuário.
 *
 * Funcionalidades:
 * - Lista todas as memórias do usuário (fetch GET /memory)
 * - Adiciona memória manualmente (POST /memory)
 * - Edita conteúdo de uma memória inline (PUT /memory/:key)
 * - Deleta uma memória específica (DELETE /memory/:key)
 * - Limpa todas as memórias (DELETE /memory) com confirmação
 */

import { Brain, Edit2, Loader2, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

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
import { m as msg } from "@/lib/paraglide/messages";
import { mDyn } from "@/lib/i18n-dyn";
// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

interface MemoryItem {
  key: string;
  content: string;
  metadata: Record<string, unknown>;
  updated_at: string;
}

interface ListMemoriesResponse {
  memories: MemoryItem[];
  total: number;
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function fetchMemories(
  limit = 50,
  offset = 0,
): Promise<ListMemoriesResponse> {
  const res = await fetch(`/memory?limit=${limit}&offset=${offset}`);
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

async function updateMemory(key: string, content: string): Promise<void> {
  const res = await fetch(`/memory/${encodeURIComponent(key)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function deleteMemory(key: string): Promise<void> {
  const res = await fetch(`/memory/${encodeURIComponent(key)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function clearAllMemories(): Promise<void> {
  const res = await fetch(`/memory`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function createMemory(key: string, content: string): Promise<void> {
  const res = await fetch(`/memory`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, content }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(
      (data as { detail?: string }).detail ?? `Erro ${res.status}`,
    );
  }
}

// ---------------------------------------------------------------------------
// Componente principal
// ---------------------------------------------------------------------------

export function MemoriaTab() {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Estado de edição
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);

  // Estado de exclusão
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false);
  const [clearing, setClearing] = useState(false);

  // Estado de criação
  const [addOpen, setAddOpen] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newContent, setNewContent] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchMemories();
      setMemories(data.memories);
      setTotal(data.total);
    } catch {
      setError(msg.memory_error_load());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Carrega memórias do backend ao montar, não estado derivado.
    // oxlint-disable-next-line react/set-state-in-effect
    void load();
  }, [load]);

  const handleEditStart = (mem: MemoryItem) => {
    setEditingKey(mem.key);
    setEditContent(mem.content);
  };

  const handleEditSave = async () => {
    if (!editingKey) return;
    setSaving(true);
    try {
      await updateMemory(editingKey, editContent);
      setMemories((prev) =>
        prev.map((m) =>
          m.key === editingKey ? { ...m, content: editContent } : m,
        ),
      );
      setEditingKey(null);
    } catch {
      setError(msg.memory_error_save());
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (key: string) => {
    setDeletingKey(key);
    try {
      await deleteMemory(key);
      setMemories((prev) => prev.filter((m) => m.key !== key));
      setTotal((prev) => prev - 1);
    } catch {
      setError(msg.memory_error_delete());
    } finally {
      setDeletingKey(null);
    }
  };

  const handleAddMemory = async () => {
    if (!newContent.trim()) return;
    // Memória é "global" do ponto de vista do usuário (como ChatGPT/Claude/Gemini):
    // não pedimos uma chave. Geramos uma internamente quando o usuário não dá rótulo.
    const key =
      newKey.trim() ||
      `mem_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    setAdding(true);
    try {
      await createMemory(key, newContent.trim());
      await load();
      setAddOpen(false);
      setNewKey("");
      setNewContent("");
    } catch (err) {
      setError(err instanceof Error ? err.message : msg.memory_error_create());
    } finally {
      setAdding(false);
    }
  };

  const handleClearAll = async () => {
    setClearing(true);
    try {
      await clearAllMemories();
      setMemories([]);
      setTotal(0);
      setClearConfirmOpen(false);
    } catch {
      setError(msg.memory_error_clear());
    } finally {
      setClearing(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const headerTitle =
    total > 0
      ? mDyn(total === 1 ? "memory.count_one" : "memory.count_many", {
          n: total,
        })
      : msg.memory_empty_title();

  return (
    <div className="space-y-4">
      {/* Cabeçalho */}
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <p className="text-sm font-medium">{headerTitle}</p>
          <p className="text-xs text-muted-foreground">
            {msg.memory_subtitle()}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
            <Plus className="w-3.5 h-3.5 mr-1.5" />
            {msg.memory_add()}
          </Button>
          {memories.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive dark:hover:text-destructive"
              onClick={() => setClearConfirmOpen(true)}
            >
              <Trash2 className="w-3.5 h-3.5 mr-1.5" />
              {msg.memory_clear_all()}
            </Button>
          )}
        </div>
      </div>

      {/* Erro */}
      {error && (
        <p className="text-xs text-destructive bg-destructive/10 px-3 py-2 rounded-md">
          {error}
        </p>
      )}

      {/* Empty state */}
      {memories.length === 0 && !loading && (
        <div className="flex flex-col items-center justify-center py-10 text-center space-y-3">
          <Brain className="w-10 h-10 text-muted-foreground/30" />
          <p className="text-sm text-muted-foreground">
            {msg.memory_empty_hint()}
          </p>
          <p className="text-xs text-muted-foreground/70 max-w-[260px]">
            {msg.memory_empty_hint2()}
          </p>
        </div>
      )}

      {/* Lista de memórias */}
      <div className="space-y-2">
        {memories.map((mem) => (
          <div
            key={mem.key}
            className="rounded-lg border bg-card p-3 space-y-2"
          >
            {/* Key + data */}
            <div className="flex items-start justify-between gap-2">
              <span className="text-xs font-mono text-muted-foreground truncate">
                {mem.key}
              </span>
              {mem.updated_at && (
                <span className="text-[10px] text-muted-foreground/60 shrink-0">
                  {new Date(mem.updated_at).toLocaleDateString("pt-BR")}
                </span>
              )}
            </div>

            {/* Conteúdo ou editor inline */}
            {editingKey === mem.key ? (
              <div className="space-y-2">
                <Textarea
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  className="text-sm min-h-[80px] resize-none"
                  autoFocus
                />
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    onClick={handleEditSave}
                    disabled={saving}
                    className="h-7 text-xs"
                  >
                    {saving && (
                      <Loader2 className="w-3 h-3 animate-spin mr-1" />
                    )}
                    {msg.memory_save()}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setEditingKey(null)}
                    disabled={saving}
                    className="h-7 text-xs"
                  >
                    {msg.memory_cancel()}
                  </Button>
                </div>
              </div>
            ) : (
              <p className="text-sm text-foreground/90 line-clamp-3">
                {mem.content}
              </p>
            )}

            {/* Ações */}
            {editingKey !== mem.key && (
              <div className="flex gap-1.5 pt-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-muted-foreground"
                  onClick={() => handleEditStart(mem)}
                >
                  <Edit2 className="w-3 h-3 mr-1" />
                  {msg.memory_edit()}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-muted-foreground hover:text-destructive"
                  onClick={() => handleDelete(mem.key)}
                  disabled={deletingKey === mem.key}
                >
                  {deletingKey === mem.key ? (
                    <Loader2 className="w-3 h-3 animate-spin mr-1" />
                  ) : (
                    <Trash2 className="w-3 h-3 mr-1" />
                  )}
                  {msg.memory_delete()}
                </Button>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Dialog — adicionar memória */}
      <Dialog
        open={addOpen}
        onOpenChange={(open) => {
          setAddOpen(open);
          if (!open) {
            setNewKey("");
            setNewContent("");
            setError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{msg.memory_add_title()}</DialogTitle>
            <DialogDescription>{msg.memory_add_desc()}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-1">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                {msg.memory_add_content_label()}
              </label>
              <Textarea
                placeholder={msg.memory_add_content_placeholder()}
                value={newContent}
                onChange={(e) => setNewContent(e.target.value)}
                className="text-sm min-h-[100px] resize-none"
                autoFocus
              />
            </div>
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer select-none hover:text-foreground">
                {msg.memory_add_key_label()}
              </summary>
              <Input
                placeholder={msg.memory_add_key_placeholder()}
                value={newKey}
                onChange={(e) => setNewKey(e.target.value)}
                autoComplete="off"
                className="text-sm mt-2"
              />
            </details>
            {error && <p className="text-xs text-destructive">{error}</p>}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setAddOpen(false)}
              disabled={adding}
            >
              {msg.memory_cancel()}
            </Button>
            <Button
              onClick={handleAddMemory}
              disabled={adding || !newContent.trim()}
            >
              {adding && <Loader2 className="w-4 h-4 animate-spin mr-2" />}
              {msg.memory_save()}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Dialog de confirmação — limpar tudo */}
      <Dialog open={clearConfirmOpen} onOpenChange={setClearConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{msg.memory_clear_title()}</DialogTitle>
            <DialogDescription>{msg.memory_clear_desc()}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setClearConfirmOpen(false)}
              disabled={clearing}
            >
              {msg.memory_cancel()}
            </Button>
            <Button
              variant="destructive"
              onClick={handleClearAll}
              disabled={clearing}
            >
              {clearing && <Loader2 className="w-4 h-4 animate-spin mr-2" />}
              {msg.memory_clear_all()}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
