import { describe, expect, it, beforeEach } from "vitest";
import {
  CONTEXT_GRAPH_PANEL_MAX_WIDTH,
  CONTEXT_GRAPH_PANEL_MIN_WIDTH,
  useContextGraphSettingsStore,
} from "../context-graph-settings-store";

describe("context-graph-settings-store", () => {
  beforeEach(() => {
    useContextGraphSettingsStore.setState({ communityPanelWidths: {} });
  });

  it("clamps and persists the community panel width per workspace", () => {
    const { setCommunityPanelWidth } = useContextGraphSettingsStore.getState();

    setCommunityPanelWidth("workspace-a", 99);
    setCommunityPanelWidth("workspace-b", 999);

    const state = useContextGraphSettingsStore.getState();
    expect(state.communityPanelWidths["workspace-a"]).toBe(
      CONTEXT_GRAPH_PANEL_MIN_WIDTH,
    );
    expect(state.communityPanelWidths["workspace-b"]).toBe(
      CONTEXT_GRAPH_PANEL_MAX_WIDTH,
    );
  });
});
