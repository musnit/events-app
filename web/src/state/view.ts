import { createContext, useContext } from "react";
import type { Filters } from "../lib/filters.ts";
import type { Route } from "../lib/routes.ts";
import type { AreaChoice } from "../lib/types.ts";

/** The route and filters a view renders. Usually the URL's; for a list under an event sheet, the list's own. */
export interface ViewLocation {
  route: Route;
  filters: Filters;
  /** Set when the view belongs to one area (the Cape Town tab), whatever the saved preference says. */
  area?: AreaChoice;
}

export const ViewContext = createContext<ViewLocation | null>(null);

export function useView(): ViewLocation {
  const view = useContext(ViewContext);
  if (!view) throw new Error("useView outside a ViewContext");
  return view;
}

/** The area of the view being drawn, or undefined outside one (the shell) and in views that follow the preference. */
export function useViewArea(): AreaChoice | undefined {
  return useContext(ViewContext)?.area;
}
