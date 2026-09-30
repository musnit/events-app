import { useEffect, useRef, type ReactNode } from "react";
import { Icon } from "./Icon.tsx";

const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex='-1'])";

/** Shows a modal panel, a bottom sheet on phones and a side or centered panel on wide screens. It traps focus and closes on Escape. */
export function Sheet({ label, onClose, children, side = "right", footer }: {
  label: string;
  onClose: () => void;
  children: ReactNode;
  side?: "right" | "bottom";
  footer?: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const root = document.documentElement;
    root.classList.add("sheet-open");
    panel.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        close.current();
      } else if (event.key === "Tab" && panel.current) {
        const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      root.classList.remove("sheet-open");
      previous?.focus?.({ preventScroll: true });
    };
  }, []);

  return (
    <div className={`sheet-layer sheet-${side}`}>
      <div className="sheet-backdrop" onClick={() => close.current()} aria-hidden="true" />
      <div className="sheet-panel" role="dialog" aria-modal="true" aria-label={label} tabIndex={-1} ref={panel}>
        <div className="sheet-top">
          <button type="button" className="icon-button" onClick={() => close.current()} aria-label="Close">
            <Icon name="close" />
          </button>
        </div>
        <div className="sheet-content">{children}</div>
        {footer && <div className="sheet-footer">{footer}</div>}
      </div>
    </div>
  );
}
