"use client";

/**
 * FileEditor — editor de código (Monaco) usado pelas janelas flutuantes.
 *
 * Carrega o conteúdo do `GET /file`, edita com syntax highlighting e tema
 * VSCode (dark/light conforme o theme do app) e salva via `PUT /fs/file` com
 * `expected_sha256` (conflito otimista → HTTP 412). Mídia e arquivos
 * truncados/binários caem no `FileViewer` read-only.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { formatEditorText, lintEditorText } from "@/lib/editor-services";
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
  const autoSaveMode = useSettingsStore((s) => s.editorAutoSaveMode);
  const autoSaveDelay = useSettingsStore((s) => s.editorAutoSaveDelay);
  const editorMinimap = useSettingsStore((s) => s.editorMinimap);
  const editorWordWrap = useSettingsStore((s) => s.editorWordWrap);
  const editorFormatOnType = useSettingsStore((s) => s.editorFormatOnType);
  const editorFormatterEnabled = useSettingsStore(
    (s) => s.editorFormatterEnabled,
  );
  const editorLinterEnabled = useSettingsStore((s) => s.editorLinterEnabled);
  const editorInlineSuggestions = useSettingsStore(
    (s) => s.editorInlineSuggestions,
  );
  const editorBreadcrumbs = useSettingsStore((s) => s.editorBreadcrumbs);
  const editorFileWatcherEnabled = useSettingsStore(
    (s) => s.editorFileWatcherEnabled,
  );
  const editorEndOfLine = useSettingsStore((s) => s.editorEndOfLine);
  const editorEncoding = useSettingsStore((s) => s.editorEncoding);
  const editorQuickSuggestions = useSettingsStore(
    (s) => s.editorQuickSuggestions,
  );
  const editorLineNumbers = useSettingsStore((s) => s.editorLineNumbers);
  const editorTabSize = useSettingsStore((s) => s.editorTabSize);
  const editorRenderWhitespace = useSettingsStore(
    (s) => s.editorRenderWhitespace,
  );
  const editorStickyScroll = useSettingsStore((s) => s.editorStickyScroll);
  const editorSmoothScrolling = useSettingsStore(
    (s) => s.editorSmoothScrolling,
  );
  const editorFontLigatures = useSettingsStore((s) => s.editorFontLigatures);
  const editorGlyphMargin = useSettingsStore((s) => s.editorGlyphMargin);
  const editorBracketPairGuides = useSettingsStore(
    (s) => s.editorBracketPairGuides,
  );
  const editorInsertSpaces = useSettingsStore((s) => s.editorInsertSpaces);
  const editorParameterHints = useSettingsStore((s) => s.editorParameterHints);
  const editorCursorStyle = useSettingsStore((s) => s.editorCursorStyle);
  const media = getMediaKind(path);

  const [file, setFile] = useState<FileContent | null>(null);
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const shaRef = useRef<string | null>(null);
  const requestEpochRef = useRef(0);
  const saveRef = useRef<() => Promise<void>>(async () => undefined);
  const autoSaveModeRef = useRef(autoSaveMode);
  const key = editorKey(workspaceId, path);

  const dirty = file?.content !== undefined && value !== file.content;
  const diagnostics = useMemo(
    () => (editorLinterEnabled ? lintEditorText(path, value) : []),
    [editorLinterEnabled, path, value],
  );
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

  useEffect(() => {
    if (!editorFileWatcherEnabled || media || readOnly || dirty) return;
    const timer = window.setInterval(() => {
      void fetchFile(workspaceId, path).then((latest) => {
        if (!latest || latest.sha256 === shaRef.current || dirty) return;
        setFile(latest);
        setValue(latest.content ?? "");
        shaRef.current = latest.sha256 ?? null;
      });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [dirty, editorFileWatcherEnabled, media, path, readOnly, workspaceId]);

  const handleSave = useCallback(async () => {
    if (!file || file.content === undefined || readOnly || saving) return;
    let contentToSave = editorFormatterEnabled
      ? formatEditorText(path, value)
      : value;
    contentToSave = contentToSave.replace(/\r\n|\r|\n/g, "\n");
    if (editorEndOfLine === "crlf")
      contentToSave = contentToSave.replace(/\n/g, "\r\n");
    if (editorEncoding === "utf8bom" && !contentToSave.startsWith("\ufeff")) {
      contentToSave = `\ufeff${contentToSave}`;
    }
    if (contentToSave !== value) setValue(contentToSave);
    setSaving(true);
    const result = await apiUpdateFile(
      workspaceId,
      path,
      contentToSave,
      shaRef.current,
    );
    setSaving(false);
    if (result.ok) {
      shaRef.current = result.sha256;
      setFile((prev) => (prev ? { ...prev, content: contentToSave } : prev));
      editorBuffers.set(key, {
        file: { ...file, content: contentToSave },
        value: contentToSave,
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
  }, [
    editorEncoding,
    editorEndOfLine,
    editorFormatterEnabled,
    file,
    key,
    path,
    readOnly,
    saving,
    value,
    workspaceId,
  ]);

  useEffect(() => {
    saveRef.current = handleSave;
    autoSaveModeRef.current = autoSaveMode;
  }, [autoSaveMode, handleSave]);

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
    if (autoSaveMode !== "afterDelay" || !dirty || readOnly) return;
    const timer = window.setTimeout(() => void handleSave(), autoSaveDelay);
    return () => window.clearTimeout(timer);
  }, [autoSaveDelay, autoSaveMode, dirty, handleSave, readOnly]);

  const handleMount: OnMount = useCallback(
    (editor, monaco) => {
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        void handleSave();
      });
      editor.addCommand(
        monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyF,
        () => {
          if (!editorFormatterEnabled || readOnly) return;
          const next = formatEditorText(path, editor.getValue());
          if (next !== editor.getValue()) editor.setValue(next);
        },
      );
      editor.onDidBlurEditorText(() => {
        if (autoSaveModeRef.current === "onFocusChange") void saveRef.current();
      });
    },
    [editorFormatterEnabled, handleSave, path, readOnly],
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
      {editorBreadcrumbs && (
        <div className="shrink-0 border-b border-border/60 px-2 py-1 text-[10px] text-muted-foreground">
          {path.split(/[\\/]/).join(" › ")}
        </div>
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
            {
              minimap: editorMinimap,
              wordWrap: editorWordWrap,
              formatOnType: editorFormatOnType,
              quickSuggestions: editorQuickSuggestions,
              lineNumbers: editorLineNumbers,
              tabSize: editorTabSize,
              renderWhitespace: editorRenderWhitespace,
              stickyScroll: editorStickyScroll,
              smoothScrolling: editorSmoothScrolling,
              fontLigatures: editorFontLigatures,
              glyphMargin: editorGlyphMargin,
              bracketPairGuides: editorBracketPairGuides,
              insertSpaces: editorInsertSpaces,
              parameterHints: editorParameterHints,
              cursorStyle: editorCursorStyle,
              inlineSuggestions: editorInlineSuggestions,
            },
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
      {diagnostics.length > 0 && (
        <div className="shrink-0 border-t border-border/60 px-2 py-1 text-[10px] text-amber-600">
          {m.workbench_files_diagnostics({
            count: diagnostics.length,
            message: diagnostics[0]?.message ?? "",
            line: diagnostics[0]?.line ?? 0,
          })}
        </div>
      )}
    </div>
  );
}
