"use client";

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useSettingsStore } from "@/lib/stores/settings-store";
import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";
import { m } from "@/lib/paraglide/messages";
import { useEffect, useState } from "react";

function DraftNumberInput({
  id,
  value,
  onCommit,
  min,
  max,
  step,
  className,
}: {
  id: string;
  value: number;
  onCommit: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  className: string;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  return (
    <Input
      id={id}
      type="number"
      min={min}
      max={max}
      step={step}
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        const parsed = Number(draft);
        if (!Number.isFinite(parsed)) {
          setDraft(String(value));
          return;
        }
        onCommit(parsed);
      }}
      className={className}
    />
  );
}

/** Render an accessible preference toggle with its explanation. */
function Toggle({
  id,
  label,
  help,
  checked,
  onChange,
}: {
  id: string;
  label: string;
  help: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <Label htmlFor={id}>{label}</Label>
        <p className="text-xs text-muted-foreground">{help}</p>
      </div>
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
    </div>
  );
}

/** Edit persisted Monaco, formatting, lint and file preferences. */
export function FileSystemSettingsForm(_context: WorkbenchSettingsContext) {
  const settings = useSettingsStore();
  const [fontSizeDraft, setFontSizeDraft] = useState(() =>
    String(settings.monacoFontSize),
  );
  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <Label htmlFor="files-autosave-mode">
          {m.workbench_files_autosave_label()}
        </Label>
        <p className="text-xs text-muted-foreground">
          {m.workbench_files_autosave_help()}
        </p>
        <Select
          value={settings.editorAutoSaveMode}
          onValueChange={(value) =>
            settings.setEditorAutoSaveMode(
              value as "off" | "afterDelay" | "onFocusChange",
            )
          }
        >
          <SelectTrigger id="files-autosave-mode">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="off">
              {m.workbench_files_autosave_off()}
            </SelectItem>
            <SelectItem value="afterDelay">
              {m.workbench_files_autosave_after_delay()}
            </SelectItem>
            <SelectItem value="onFocusChange">
              {m.workbench_files_autosave_on_focus_change()}
            </SelectItem>
          </SelectContent>
        </Select>
      </div>
      {settings.editorAutoSaveMode === "afterDelay" && (
        <div className="flex items-center justify-between gap-3">
          <div>
            <Label htmlFor="files-autosave-delay">
              {m.workbench_files_autosave_delay_label()}
            </Label>
            <p className="text-xs text-muted-foreground">
              {m.workbench_files_autosave_delay_help()}
            </p>
          </div>
          <DraftNumberInput
            id="files-autosave-delay"
            value={settings.editorAutoSaveDelay}
            onCommit={settings.setEditorAutoSaveDelay}
            min={200}
            max={5000}
            step={100}
            className="w-24"
          />
        </div>
      )}
      <div className="flex items-center justify-between gap-3">
        <div>
          <Label htmlFor="files-font-size">
            {m.workbench_files_font_size_label()}
          </Label>
          <p className="text-xs text-muted-foreground">
            {m.workbench_files_font_size_help()}
          </p>
        </div>
        <Input
          id="files-font-size"
          type="number"
          min={10}
          max={24}
          value={fontSizeDraft}
          onChange={(event) => setFontSizeDraft(event.target.value)}
          onBlur={() => {
            const value = Number(fontSizeDraft);
            if (Number.isFinite(value)) settings.setMonacoFontSize(value);
            else setFontSizeDraft(String(settings.monacoFontSize));
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur();
          }}
          className="w-24"
        />
      </div>
      <div className="flex items-center justify-between gap-3">
        <div>
          <Label htmlFor="files-max-size">
            {m.workbench_files_max_size_label()}
          </Label>
          <p className="text-xs text-muted-foreground">
            {m.workbench_files_max_size_help()}
          </p>
        </div>
        <DraftNumberInput
          id="files-max-size"
          value={settings.editorMaxFileSizeMb}
          onCommit={settings.setEditorMaxFileSizeMb}
          min={1}
          max={100}
          className="w-24"
        />
      </div>
      <div className="flex items-center justify-between gap-3">
        <div>
          <Label htmlFor="files-font-family">
            {m.workbench_files_font_label()}
          </Label>
        </div>
        <Input
          id="files-font-family"
          value={settings.editorFontFamily}
          onChange={(event) => settings.setEditorFontFamily(event.target.value)}
          className="w-64"
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {m.workbench_files_editor_options_hint()}
      </p>
      <Toggle
        id="files-minimap"
        label={m.workbench_files_minimap_label()}
        help={m.workbench_files_minimap_help()}
        checked={settings.editorMinimap}
        onChange={settings.setEditorMinimap}
      />
      <Toggle
        id="files-word-wrap"
        label={m.workbench_files_word_wrap_label()}
        help={m.workbench_files_word_wrap_help()}
        checked={settings.editorWordWrap}
        onChange={settings.setEditorWordWrap}
      />
      <Toggle
        id="files-format-on-type"
        label={m.workbench_files_format_on_type_label()}
        help={m.workbench_files_format_on_type_help()}
        checked={settings.editorFormatOnType}
        onChange={settings.setEditorFormatOnType}
      />
      <Toggle
        id="files-formatter-service"
        label={m.workbench_files_formatter_label()}
        help={m.workbench_files_formatter_help()}
        checked={settings.editorFormatterEnabled}
        onChange={settings.setEditorFormatterEnabled}
      />
      <Toggle
        id="files-format-on-save"
        label={m.workbench_files_format_on_save_label()}
        help={m.workbench_files_format_on_save_help()}
        checked={settings.editorFormatOnSave}
        onChange={settings.setEditorFormatOnSave}
      />
      <Toggle
        id="files-linter-service"
        label={m.workbench_files_linter_label()}
        help={m.workbench_files_linter_help()}
        checked={settings.editorLinterEnabled}
        onChange={settings.setEditorLinterEnabled}
      />
      <Toggle
        id="files-lint-on-type"
        label={m.workbench_files_lint_on_type_label()}
        help={m.workbench_files_lint_on_type_help()}
        checked={settings.editorLintOnType}
        onChange={settings.setEditorLintOnType}
      />
      <Toggle
        id="files-lint-on-save"
        label={m.workbench_files_lint_on_save_label()}
        help={m.workbench_files_lint_on_save_help()}
        checked={settings.editorLintOnSave}
        onChange={settings.setEditorLintOnSave}
      />
      <Toggle
        id="files-inline-suggestions"
        label={m.workbench_files_inline_suggestions()}
        help={m.workbench_files_inline_suggestions_help()}
        checked={settings.editorInlineSuggestions}
        onChange={settings.setEditorInlineSuggestions}
      />
      <Toggle
        id="files-breadcrumbs"
        label={m.workbench_files_breadcrumbs()}
        help={m.workbench_files_breadcrumbs_help()}
        checked={settings.editorBreadcrumbs}
        onChange={settings.setEditorBreadcrumbs}
      />
      <Toggle
        id="files-file-watcher"
        label={m.workbench_files_file_watcher()}
        help={m.workbench_files_file_watcher_help()}
        checked={settings.editorFileWatcherEnabled}
        onChange={settings.setEditorFileWatcherEnabled}
      />
      <Toggle
        id="files-confirm-delete"
        label={m.workbench_files_confirm_delete()}
        help={m.workbench_files_confirm_delete_help()}
        checked={settings.editorConfirmDelete}
        onChange={settings.setEditorConfirmDelete}
      />
      <div className="space-y-1">
        <Label htmlFor="files-editor-theme">
          {m.workbench_files_editor_theme()}
        </Label>
        <Select
          value={settings.editorTheme}
          onValueChange={(value) =>
            settings.setEditorTheme(value as "auto" | "light" | "dark")
          }
        >
          <SelectTrigger id="files-editor-theme">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="auto">
              {m.workbench_files_theme_auto()}
            </SelectItem>
            <SelectItem value="light">
              {m.workbench_files_theme_light()}
            </SelectItem>
            <SelectItem value="dark">
              {m.workbench_files_theme_dark()}
            </SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label htmlFor="files-encoding">{m.workbench_files_encoding()}</Label>
          <Select
            value={settings.editorEncoding}
            onValueChange={(value) =>
              settings.setEditorEncoding(value as "utf8" | "utf8bom")
            }
          >
            <SelectTrigger id="files-encoding">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="utf8">{m.workbench_files_utf8()}</SelectItem>
              <SelectItem value="utf8bom">
                {m.workbench_files_utf8bom()}
              </SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="files-eol">{m.workbench_files_eol()}</Label>
          <Select
            value={settings.editorEndOfLine}
            onValueChange={(value) =>
              settings.setEditorEndOfLine(value as "lf" | "crlf")
            }
          >
            <SelectTrigger id="files-eol">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="lf">{m.workbench_files_lf()}</SelectItem>
              <SelectItem value="crlf">{m.workbench_files_crlf()}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <Toggle
        id="files-quick-suggestions"
        label={m.workbench_files_quick_suggestions_label()}
        help={m.workbench_files_quick_suggestions_help()}
        checked={settings.editorQuickSuggestions}
        onChange={settings.setEditorQuickSuggestions}
      />
      <Toggle
        id="files-parameter-hints"
        label={m.workbench_files_parameter_hints_label()}
        help={m.workbench_files_parameter_hints_help()}
        checked={settings.editorParameterHints}
        onChange={settings.setEditorParameterHints}
      />
      <Toggle
        id="files-font-ligatures"
        label={m.workbench_files_font_ligatures_label()}
        help={m.workbench_files_font_ligatures_help()}
        checked={settings.editorFontLigatures}
        onChange={settings.setEditorFontLigatures}
      />
      <Toggle
        id="files-glyph-margin"
        label={m.workbench_files_glyph_margin_label()}
        help={m.workbench_files_glyph_margin_help()}
        checked={settings.editorGlyphMargin}
        onChange={settings.setEditorGlyphMargin}
      />
      <Toggle
        id="files-line-numbers"
        label={m.workbench_files_line_numbers_label()}
        help={m.workbench_files_line_numbers_help()}
        checked={settings.editorLineNumbers}
        onChange={settings.setEditorLineNumbers}
      />
      <div className="flex items-center justify-between gap-3">
        <div>
          <Label htmlFor="files-tab-size">
            {m.workbench_files_tab_size_label()}
          </Label>
          <p className="text-xs text-muted-foreground">
            {m.workbench_files_tab_size_help()}
          </p>
        </div>
        <DraftNumberInput
          id="files-tab-size"
          value={settings.editorTabSize}
          onCommit={settings.setEditorTabSize}
          min={1}
          max={8}
          className="w-20"
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="files-whitespace">
          {m.workbench_files_whitespace_label()}
        </Label>
        <Select
          value={settings.editorRenderWhitespace}
          onValueChange={(value) =>
            settings.setEditorRenderWhitespace(
              value as "none" | "selection" | "all",
            )
          }
        >
          <SelectTrigger id="files-whitespace">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">
              {m.workbench_files_whitespace_none()}
            </SelectItem>
            <SelectItem value="selection">
              {m.workbench_files_whitespace_selection()}
            </SelectItem>
            <SelectItem value="all">
              {m.workbench_files_whitespace_all()}
            </SelectItem>
          </SelectContent>
        </Select>
      </div>
      <Toggle
        id="files-sticky-scroll"
        label={m.workbench_files_sticky_scroll_label()}
        help={m.workbench_files_sticky_scroll_help()}
        checked={settings.editorStickyScroll}
        onChange={settings.setEditorStickyScroll}
      />
      <Toggle
        id="files-bracket-guides"
        label={m.workbench_files_bracket_guides_label()}
        help={m.workbench_files_bracket_guides_help()}
        checked={settings.editorBracketPairGuides}
        onChange={settings.setEditorBracketPairGuides}
      />
      <Toggle
        id="files-insert-spaces"
        label={m.workbench_files_insert_spaces_label()}
        help={m.workbench_files_insert_spaces_help()}
        checked={settings.editorInsertSpaces}
        onChange={settings.setEditorInsertSpaces}
      />
      <div className="space-y-1">
        <Label htmlFor="files-cursor-style">
          {m.workbench_files_cursor_style_label()}
        </Label>
        <Select
          value={settings.editorCursorStyle}
          onValueChange={(value) =>
            settings.setEditorCursorStyle(
              value as "line" | "block" | "underline",
            )
          }
        >
          <SelectTrigger id="files-cursor-style">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="line">
              {m.workbench_files_cursor_line()}
            </SelectItem>
            <SelectItem value="block">
              {m.workbench_files_cursor_block()}
            </SelectItem>
            <SelectItem value="underline">
              {m.workbench_files_cursor_underline()}
            </SelectItem>
          </SelectContent>
        </Select>
      </div>
      <Toggle
        id="files-smooth-scrolling"
        label={m.workbench_files_smooth_scrolling_label()}
        help={m.workbench_files_smooth_scrolling_help()}
        checked={settings.editorSmoothScrolling}
        onChange={settings.setEditorSmoothScrolling}
      />
    </div>
  );
}

/** Configure plan display and ordering preferences. */
export function PlanSettingsForm(_context: WorkbenchSettingsContext) {
  const settings = useSettingsStore();
  return (
    <div className="space-y-4">
      <Toggle
        id="plan-auto-expand"
        label={m.workbench_plan_auto_expand_label()}
        help={m.workbench_plan_auto_expand_help()}
        checked={settings.planAutoExpand}
        onChange={settings.setPlanAutoExpand}
      />
      <div className="space-y-1">
        <Label htmlFor="plan-sort">{m.workbench_plan_sort_label()}</Label>
        <Select
          value={settings.planSort}
          onValueChange={(value) =>
            settings.setPlanSort(value as "created" | "title")
          }
        >
          <SelectTrigger id="plan-sort">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="created">
              {m.workbench_plan_sort_created()}
            </SelectItem>
            <SelectItem value="title">
              {m.workbench_plan_sort_title()}
            </SelectItem>
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}

/** Configure notifications, bounded retries and task concurrency. */
export function TasksSettingsForm(_context: WorkbenchSettingsContext) {
  const settings = useSettingsStore();
  const [retryDraft, setRetryDraft] = useState(() =>
    String(settings.taskRetryCount),
  );
  const [concurrencyDraft, setConcurrencyDraft] = useState(() =>
    String(settings.taskConcurrency),
  );
  const commitNumber = (
    draft: string,
    commit: (value: number) => void,
    reset: () => void,
  ) => {
    const value = Number(draft);
    if (Number.isFinite(value)) commit(value);
    else reset();
  };
  return (
    <div className="space-y-4">
      <Toggle
        id="tasks-notifications"
        label={m.workbench_tasks_notifications_label()}
        help={m.workbench_tasks_notifications_help()}
        checked={settings.taskNotifications}
        onChange={settings.setTaskNotifications}
      />
      <div className="flex items-center justify-between gap-3">
        <div>
          <Label htmlFor="tasks-retry">{m.workbench_tasks_retry_label()}</Label>
          <p className="text-xs text-muted-foreground">
            {m.workbench_tasks_retry_help()}
          </p>
        </div>
        <Input
          id="tasks-retry"
          type="number"
          min={0}
          max={5}
          value={retryDraft}
          onChange={(event) => setRetryDraft(event.target.value)}
          onBlur={() =>
            commitNumber(retryDraft, settings.setTaskRetryCount, () =>
              setRetryDraft(String(settings.taskRetryCount)),
            )
          }
          className="w-24"
        />
      </div>
      <div className="flex items-center justify-between gap-3">
        <div>
          <Label htmlFor="tasks-concurrency">
            {m.workbench_tasks_concurrency_label()}
          </Label>
          <p className="text-xs text-muted-foreground">
            {m.workbench_tasks_concurrency_help()}
          </p>
        </div>
        <Input
          id="tasks-concurrency"
          type="number"
          min={1}
          max={8}
          value={concurrencyDraft}
          onChange={(event) => setConcurrencyDraft(event.target.value)}
          onBlur={() =>
            commitNumber(concurrencyDraft, settings.setTaskConcurrency, () =>
              setConcurrencyDraft(String(settings.taskConcurrency)),
            )
          }
          className="w-24"
        />
      </div>
      <div className="flex items-center justify-between gap-3">
        <div>
          <Label htmlFor="tasks-backoff">
            {m.workbench_tasks_backoff_label()}
          </Label>
          <p className="text-xs text-muted-foreground">
            {m.workbench_tasks_backoff_help()}
          </p>
        </div>
        <DraftNumberInput
          id="tasks-backoff"
          value={settings.taskRetryBackoffMs}
          onCommit={settings.setTaskRetryBackoffMs}
          min={100}
          max={30000}
          step={100}
          className="w-24"
        />
      </div>
    </div>
  );
}

/** Configure library ordering and visibility preferences. */
export function LibrarySettingsForm(_context: WorkbenchSettingsContext) {
  const settings = useSettingsStore();
  return (
    <div className="space-y-4">
      <Toggle
        id="library-skills"
        label={m.workbench_library_skills_label()}
        help={m.workbench_library_skills_help()}
        checked={settings.libraryShowSkills}
        onChange={settings.setLibraryShowSkills}
      />
      <Toggle
        id="library-mcp"
        label={m.workbench_library_mcp_label()}
        help={m.workbench_library_mcp_help()}
        checked={settings.libraryShowMcp}
        onChange={settings.setLibraryShowMcp}
      />
    </div>
  );
}
