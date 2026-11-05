// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExtensionsSection } from "../library-extensions-section";
import {
  useLibraryStore,
  type VextExtension,
} from "@/lib/stores/library-store";

afterEach(cleanup);

const EXTENSION: VextExtension = {
  id: "sample",
  name: "Sample extension",
  description: "A registry extension",
  publisher: "Vectora",
  version: "1.0.0",
  runtime: "node",
  platforms: "any",
  permissions: "",
  digest: "digest",
  status: "published",
};

describe("ExtensionsSection", () => {
  it("renders a catalog card and its version", () => {
    useLibraryStore.setState({
      extensionItems: [EXTENSION],
      extensionLoading: false,
      extensionError: null,
      extensionInstalledIds: new Set(),
      ensureExtensionsLoaded: vi.fn(),
    });
    render(<ExtensionsSection query="" onCountChange={vi.fn()} />);
    expect(screen.getByText("Sample extension")).toBeTruthy();
    expect(screen.getByText(/1\.0\.0/)).toBeTruthy();
  });

  it("renders loading, error and empty states", () => {
    const view = render(<ExtensionsSection query="" onCountChange={vi.fn()} />);
    useLibraryStore.setState({ extensionLoading: true });
    view.rerender(<ExtensionsSection query="" onCountChange={vi.fn()} />);
    expect(screen.getByText(/Loading extensions/)).toBeTruthy();
    useLibraryStore.setState({
      extensionLoading: false,
      extensionError: "offline",
    });
    view.rerender(<ExtensionsSection query="" onCountChange={vi.fn()} />);
    expect(screen.getByText("offline")).toBeTruthy();
    useLibraryStore.setState({ extensionError: null, extensionItems: [] });
    view.rerender(<ExtensionsSection query="" onCountChange={vi.fn()} />);
    expect(screen.getByText(/No extensions found/)).toBeTruthy();
  });
});
