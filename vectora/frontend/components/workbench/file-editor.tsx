"use client";

/**
 * FileEditor — editor de código (Monaco) usado pelas janelas flutuantes.
 *
 * Carrega o conteúdo do `GET /file`, edita com syntax highlighting e tema
 * VSCode (dark/light conforme o theme do app) e salva via `PUT /fs/file` com
 * `expected_sha256` (conflito otimista → HTTP 412). Mídia e arquivos
 * truncados/binários caem no `FileViewer` read-only.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import MonacoEditor, { type OnMount } from "@monaco-editor/react";
import { Loader2 } from "lucide-react";

import { languageFromPath } from "@/lib/monaco/setup";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { fetchFile, apiUpdateFile } from "@/lib/api/fs-files";
import { apiFsCreateFile } from "@/components/workbench/files/files-api";
import type { FileContent } from "@/lib/stores/workbench-store";
import { useToastStore } from "@/lib/stores/toast-store";
import { getMediaKind, FileViewer } from "@/components/workbench/file-viewer";
import { m } from "@/lib/paraglide/messages";
import { useMonacoTheme } from "@/lib/monaco/use-monaco-theme";
import { godotEditorOptions } from "@/lib/monaco/editor-options";
import {
  editorBuffers,
  editorKey,
  useEditorRegistry,
} from "@/lib/stores/editor-registry";

export function FileEditor({
  workspaceId,
  path,
}: {
  workspaceId: string;
  path: string;
}) {
  const language = languageFromPath(path);
  const monacoTheme = useMonacoTheme(language);
  const monacoFontSize = useSettingsStore((s) => s.monacoFontSize);
  const editorFontFamily = useSettingsStore((s) => s.editorFontFamily);
  const autoSave = useSettingsStore((s) => s.editorAutoSave);
  const media = getMediaKind(path);

  const [file, setFile] = useState<FileContent | null>(null);
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const shaRef = useRef<string | null>(null);
  const requestEpochRef = useRef(0);
  const key = editorKey(workspaceId, path);

  const dirty = file?.content !== undefined && value !== file.content;
  const readOnly =
    file?.kind === "binary" || file?.truncated || file?.sha256 == null;

  useEffect(() => {
    if (media) return;
    const requestEpoch = ++requestEpochRef.current;
    let cancelled = false;
    const buffered = editorBuffers.get(editorKey(workspaceId, path));
    if (buffered) {
      // oxlint-disable-next-line react/set-state-in-effect
      setFile(buffered.file);
      setValue(buffered.value);
      shaRef.current = buffered.sha256;
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }
    // Busca o conteúdo do arquivo no backend (rede) ao trocar de path.
    // oxlint-disable-next-line react/set-state-in-effect
    setLoading(true);
    fetchFile(workspaceId, path)
      .then((data) => {
        if (cancelled || requestEpoch !== requestEpochRef.current) return;
        setFile(data);
        setValue(data?.content ?? "");
        shaRef.current = data?.sha256 ?? null;
        if (data) {
          editorBuffers.set(editorKey(workspaceId, path), {
            file: data,
            value: data.content ?? "",
            sha256: data.sha256 ?? null,
          });
        }
      })
      .finally(() => {
        if (!cancelled && requestEpoch === requestEpochRef.current)
          setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, path, media]);

  const handleSave = useCallback(async () => {
    if (!file || file.content === undefined || readOnly || saving) return;
    setSaving(true);
    const result = await apiUpdateFile(
      workspaceId,
      path,
      value,
      shaRef.current,
    );
    setSaving(false);
    if (result.ok) {
      shaRef.current = result.sha256;
      setFile((prev) => (prev ? { ...prev, content: value } : prev));
      editorBuffers.set(key, {
        file: { ...file, content: value },
        value,
        sha256: result.sha256,
      });
      return;
    }
    useToastStore
      .getState()
      .error(
        result.conflict
          ? m.workbench_files_conflict_title()
          : m.workbench_files_save_error(),
        { description: result.message },
      );
  }, [file, key, readOnly, saving, workspaceId, path, value]);

  const handleSaveAs = useCallback(
    async (targetPath: string) => {
      if (
        !file ||
        file.content === undefined ||
        readOnly ||
        !targetPath.trim()
      ) {
        return false;
      }
      const result = await apiFsCreateFile(
        workspaceId,
        targetPath.trim(),
        value,
      );
      if (!result.ok) {
        useToastStore.getState().error(m.workbench_files_save_error(), {
          description: result.message,
        });
      }
      return result.ok;
    },
    [file, readOnly, workspaceId, value],
  );

  const registerEditor = useEditorRegistry((s) => s.register);
  const setEditorDirty = useEditorRegistry((s) => s.setDirty);

  useEffect(() => {
    setEditorDirty(key, Boolean(dirty));
  }, [dirty, key, setEditorDirty]);

  useEffect(() => {
    registerEditor(key, {
      workspaceId,
      path,
      dirty: Boolean(dirty),
      save: async () => {
        await handleSave();
        return !useEditorRegistry.getState().entries[key]?.dirty;
      },
      saveAs: handleSaveAs,
    });
  }, [dirty, handleSave, handleSaveAs, key, path, registerEditor, workspaceId]);

  useEffect(() => {
    const buffered = editorBuffers.get(key);
    if (!file || !buffered) return;
    editorBuffers.set(key, { ...buffered, file, value });
  }, [file, key, value]);

  useEffect(() => {
    if (!autoSave || !dirty || readOnly) return;
    const timer = window.setTimeout(() => void handleSave(), 800);
    return () => window.clearTimeout(timer);
  }, [autoSave, dirty, handleSave, readOnly]);

  const handleMount: OnMount = useCallback(
    (editor, monaco) => {
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        void handleSave();
      });
    },
    [handleSave],
  );

  if (media) {
    return <FileViewer workspaceId={workspaceId} path={path} />;
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (file?.kind === "binary") {
    return <FileViewer workspaceId={workspaceId} path={path} />;
  }

  return (
    <div className="relative flex h-full w-full min-w-0 flex-col">
      {dirty && (
        <span
          className="absolute right-2 top-2 z-10 h-1.5 w-1.5 rounded-full bg-amber-500"
          title={m.workbench_files_unsaved()}
        />
      )}
      <div className="min-h-0 w-full flex-1">
        <MonacoEditor
          height="100%"
          width="100%"
          value={value}
          language={language}
          theme={monacoTheme}
          onChange={(v) => setValue(v ?? "")}
          onMount={handleMount}
          options={godotEditorOptions(
            monacoFontSize,
            editorFontFamily,
            readOnly,
          )}
          loading={
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          }
        />
      </div>
      {file?.truncated && (
        <p className="shrink-0 border-t border-border/60 px-2 py-1 text-[10px] text-muted-foreground">
          {m.workbench_files_read_only_truncated()}
        </p>
      )}
    </div>
  );
}
