"use client";

import { useState } from "react";
import { m } from "#/paraglide/messages";
import {
  useApiKeys,
  useCreateApiKey,
  useRevokeApiKey,
} from "#/hooks/use-api-keys";
import { Plus, Trash2, Copy } from "lucide-react";
import { toast } from "sonner";

type Scope = "read" | "write" | "admin";
const SCOPES: Scope[] = ["read", "write", "admin"];

function CreateKeyModal({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Scope[]>(["read"]);
  const [secret, setSecret] = useState<string | null>(null);

  const mutation = useCreateApiKey();

  const toggleScope = (s: Scope) => {
    setScopes((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s],
    );
  };

  const handleCreate = () => {
    mutation.mutate(
      { name, scopes },
      {
        onSuccess: (res) => setSecret(res.secret),
        onError: () => toast.error(m.error_generic()),
      },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl border border-border bg-background p-6 shadow-xl">
        {secret ? (
          <>
            <h3 className="mb-1 text-lg font-semibold text-foreground">
              {m.apikeys_modal_secret_heading()}
            </h3>
            <p className="mb-4 text-sm text-muted-foreground">
              {m.apikey_secret_warning()}
            </p>
            <div className="flex items-center gap-2 rounded-xl border border-primary/30 bg-background px-4 py-3">
              <code className="flex-1 break-all font-mono text-sm text-primary">
                {secret}
              </code>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(secret);
                  toast.success(m.token_copied());
                }}
                className="shrink-0 rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-card transition-colors"
              >
                <Copy className="h-4 w-4" />
              </button>
            </div>
            <button
              onClick={onClose}
              className="mt-4 w-full rounded-xl border border-border py-2.5 text-sm font-medium text-foreground/90 hover:border-primary hover:text-foreground transition-all"
            >
              {m.apikeys_modal_done()}
            </button>
          </>
        ) : (
          <>
            <h3 className="mb-4 text-lg font-semibold text-foreground">
              {m.apikeys_modal_heading()}
            </h3>
            <div className="mb-4">
              <label className="mb-1.5 block text-sm font-medium text-foreground/90">
                {m.apikeys_name_label()}
              </label>
              <input
                type="text"
                required
                maxLength={64}
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded-xl border border-border bg-card/60 px-4 py-2.5 text-sm text-foreground outline-none focus:border-primary transition-colors"
              />
            </div>
            <div className="mb-6">
              <label className="mb-2 block text-sm font-medium text-foreground/90">
                {m.apikeys_scopes_label()}
              </label>
              <div className="flex gap-2">
                {SCOPES.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => toggleScope(s)}
                    className={`rounded-lg border px-3 py-1.5 text-sm transition-all ${
                      scopes.includes(s)
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border text-muted-foreground hover:text-foreground/90"
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={onClose}
                className="flex-1 rounded-xl border border-border py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground transition-all"
              >
                {m.form_cancel()}
              </button>
              <button
                onClick={handleCreate}
                disabled={
                  !name.trim() || scopes.length === 0 || mutation.isPending
                }
                className="flex-1 rounded-xl bg-primary py-2.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-all"
              >
                {mutation.isPending
                  ? m.form_submitting()
                  : m.apikeys_create_cta()}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function ApiKeysList() {
  const { data: keys, isLoading } = useApiKeys();
  const revokeMutation = useRevokeApiKey();
  const [showModal, setShowModal] = useState(false);
  const [revokeId, setRevokeId] = useState<string | null>(null);

  if (isLoading)
    return (
      <div className="h-32 max-w-3xl rounded-xl bg-card/30 animate-pulse" />
    );

  return (
    <div className="max-w-3xl">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {keys?.length ?? 0} {m.apikeys_count()}
        </p>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 transition-all"
        >
          <Plus className="h-4 w-4" />
          {m.apikeys_create_cta()}
        </button>
      </div>

      {keys?.length === 0 ? (
        <div className="rounded-xl border border-border bg-card/10 p-8 text-center text-sm text-muted-foreground">
          {m.apikeys_empty()}
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-card/50">
                {[
                  m.apikeys_col_name(),
                  m.apikeys_col_scopes(),
                  m.apikeys_col_created(),
                  m.apikeys_col_last_used(),
                  "",
                ].map((h, i) => (
                  <th
                    key={i}
                    className="px-4 py-3 text-start text-xs font-medium text-muted-foreground"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {keys?.map(
                (
                  key: {
                    id: string;
                    name: string;
                    scopes: string[];
                    created_at: string;
                    last_used_at: string | null;
                  },
                  i: number,
                ) => (
                  <tr
                    key={key.id}
                    className={`border-b border-border ${i % 2 === 0 ? "" : "bg-background/20"}`}
                  >
                    <td className="px-4 py-3 font-medium text-foreground">
                      {key.name}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {key.scopes.map((s: string) => (
                          <span
                            key={s}
                            className="rounded border border-border bg-background px-1.5 py-0.5 text-xs text-muted-foreground"
                          >
                            {s}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {new Date(key.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {key.last_used_at
                        ? new Date(key.last_used_at).toLocaleDateString()
                        : "—"}
                    </td>
                    <td className="px-4 py-3">
                      {revokeId === key.id ? (
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => {
                              revokeMutation.mutate(key.id, {
                                onSuccess: () => setRevokeId(null),
                              });
                            }}
                            disabled={revokeMutation.isPending}
                            className="text-xs text-accent-red hover:text-destructive font-medium"
                          >
                            {m.apikeys_revoke_confirm_cta()}
                          </button>
                          <button
                            onClick={() => setRevokeId(null)}
                            className="text-xs text-muted-foreground hover:text-foreground/90"
                          >
                            {m.form_cancel()}
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setRevokeId(key.id)}
                          className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent-red/10 hover:text-accent-red transition-colors"
                          title={m.apikeys_revoke_title()}
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      )}
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </div>
      )}

      {showModal && <CreateKeyModal onClose={() => setShowModal(false)} />}
    </div>
  );
}
