// Short-lived status messages ("Saved", "Could not reach the server").

import { useSyncExternalStore } from "react";

export interface Toast {
  id: number;
  text: string;
  tone: "info" | "error";
}

let toasts: Toast[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function toast(text: string, tone: Toast["tone"] = "info", ms = tone === "error" ? 6000 : 3000): void {
  const item = { id: nextId++, text, tone };
  toasts = [...toasts.slice(-2), item];
  emit();
  window.setTimeout(() => dismissToast(item.id), ms);
}

export function dismissToast(id: number): void {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => toasts,
  );
}
