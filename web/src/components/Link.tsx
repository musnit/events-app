import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from "react";
import { navigate, type NavigateOptions } from "../state/router.ts";

type Props = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  to: string;
  options?: NavigateOptions;
  children: ReactNode;
};

/** An ordinary <a href> (so open-in-new-tab and copy-link work) that navigates in-app on a plain click. */
export function Link({ to, options, onClick, children, ...rest }: Props) {
  function handle(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (rest.target && rest.target !== "_self") return;
    event.preventDefault();
    navigate(to, options);
  }
  return (
    <a href={to} onClick={handle} {...rest}>
      {children}
    </a>
  );
}
