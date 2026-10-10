import { beforeEach, describe, expect, it } from "vitest";
import { useBrowserSettingsController } from "../browser-settings-controller";

describe("browser settings controller", () => {
  beforeEach(() =>
    useBrowserSettingsController.setState({ pendingOpen: null }),
  );

  it("stores a one-shot request for the target thread", () => {
    useBrowserSettingsController
      .getState()
      .requestOpenNativeSettings("thread-1");
    expect(useBrowserSettingsController.getState().pendingOpen).toEqual({
      threadId: "thread-1",
    });
    expect(
      useBrowserSettingsController.getState().consumePendingOpen("thread-1"),
    ).toBe(true);
    expect(useBrowserSettingsController.getState().pendingOpen).toBeNull();
  });

  it("does not consume another thread request", () => {
    useBrowserSettingsController
      .getState()
      .requestOpenNativeSettings("thread-1");
    expect(
      useBrowserSettingsController.getState().consumePendingOpen("thread-2"),
    ).toBe(false);
    expect(useBrowserSettingsController.getState().pendingOpen).toEqual({
      threadId: "thread-1",
    });
  });
});
