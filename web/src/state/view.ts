import { createContext, useContext } from "react";
import type { Filters } from "../lib/filters.ts";
import type { Route } from "../lib/routes.ts";

/** The route and filters a view renders. Usually the URL's; for a list under an event sheet, the list's own. */
export interface ViewLocation {
  route: Route;
  filters: Filters;
}

export const ViewContext = createContext<ViewLocation | null>(null);

export function useView(): ViewLocation {
  const view = useContext(ViewContext);
  if (!view) throw new Error("useView outside a ViewContext");
  return view;
}
