"use client";

import { useToastStore } from "@/lib/stores/toast-store";

export function useToast() {
  const success = useToastStore((s) => s.success);
  const error = useToastStore((s) => s.error);
  const warning = useToastStore((s) => s.warning);
  const info = useToastStore((s) => s.info);

  return {
    success: (title: string, description?: string) =>
      success(title, { description }),
    error: (title: string, description?: string) =>
      error(title, { description }),
    warning: (title: string, description?: string) =>
      warning(title, { description }),
    info: (title: string, description?: string) => info(title, { description }),
  };
}
