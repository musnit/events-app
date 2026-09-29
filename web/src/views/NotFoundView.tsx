import { Link } from "../components/Link.tsx";
import { EmptyState } from "../components/Notices.tsx";
import { Page } from "../components/Page.tsx";
import { href } from "../lib/routes.ts";

export function NotFoundView() {
  return (
    <Page title="Page not found">
      <EmptyState title="There's nothing at this address">
        <p>The link may be mistyped, or the date may be invalid.</p>
        <Link className="button" to={href({ name: "agenda", from: null })}>Go to upcoming events</Link>
      </EmptyState>
    </Page>
  );
}
