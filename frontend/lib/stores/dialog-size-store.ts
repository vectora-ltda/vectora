import { create } from "zustand";
import { persist } from "zustand/middleware";

interface DialogSizeState {
  sizes: Record<string, { w: number; h: number }>;
  setSize: (key: string, size: { w: number; h: number }) => void;
  getSize: (
    key: string,
    defaults: { w: number; h: number },
  ) => { w: number; h: number };
}

export const useDialogSizeStore = create<DialogSizeState>()(
  persist(
    (set, get) => ({
      sizes: {},
      setSize: (key, size) =>
        set((state) => ({ sizes: { ...state.sizes, [key]: size } })),
      getSize: (key, defaults) => get().sizes[key] ?? defaults,
    }),
    { name: "vectora-dialog-sizes" },
  ),
);
