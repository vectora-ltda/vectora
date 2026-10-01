"use client";

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

export function FileSystemSettingsForm(_context: WorkbenchSettingsContext) {
  const settings = useSettingsStore();
  return (
    <div className="space-y-4">
      <Toggle
        id="files-autosave"
        label={m.workbench_files_autosave_label()}
        help={m.workbench_files_autosave_help()}
        checked={settings.editorAutoSave}
        onChange={settings.setEditorAutoSave}
      />
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
          value={settings.monacoFontSize}
          onChange={(event) =>
            settings.setMonacoFontSize(Number(event.target.value))
          }
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
        id="files-quick-suggestions"
        label={m.workbench_files_quick_suggestions_label()}
        help={m.workbench_files_quick_suggestions_help()}
        checked={settings.editorQuickSuggestions}
        onChange={settings.setEditorQuickSuggestions}
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
        <Input
          id="files-tab-size"
          type="number"
          min={1}
          max={8}
          value={settings.editorTabSize}
          onChange={(event) =>
            settings.setEditorTabSize(Number(event.target.value))
          }
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
        id="files-smooth-scrolling"
        label={m.workbench_files_smooth_scrolling_label()}
        help={m.workbench_files_smooth_scrolling_help()}
        checked={settings.editorSmoothScrolling}
        onChange={settings.setEditorSmoothScrolling}
      />
    </div>
  );
}

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

export function TasksSettingsForm(_context: WorkbenchSettingsContext) {
  const settings = useSettingsStore();
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
          value={settings.taskRetryCount}
          onChange={(event) =>
            settings.setTaskRetryCount(Number(event.target.value))
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
          value={settings.taskConcurrency}
          onChange={(event) =>
            settings.setTaskConcurrency(Number(event.target.value))
          }
          className="w-24"
        />
      </div>
    </div>
  );
}

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
