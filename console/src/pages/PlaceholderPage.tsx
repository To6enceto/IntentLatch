import type { ConsolePage } from "../pages";
import { Icon } from "../components/Icon";

export function PlaceholderPage({ page }: { page: ConsolePage }) {
  return (
    <>
      <div className="page-heading">
        <h1>{page.title}</h1>
        <p>{page.description}</p>
      </div>
      <section className="placeholder-card" aria-labelledby="placeholder-title">
        <span className="placeholder-icon"><Icon name={page.icon} className="size-5" /></span>
        <h2 id="placeholder-title">Coming soon</h2>
        <p>Management tools for this page will be added in a later phase.</p>
      </section>
    </>
  );
}
