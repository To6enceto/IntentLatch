import { Link } from "react-router";
import { buttonVariants } from "../components/ui/Button";

export function NotFoundPage() {
  return (
    <>
      <div className="page-heading">
        <h1>Page not found</h1>
        <p>The requested page does not exist in this console.</p>
      </div>
      <section className="placeholder-card" aria-labelledby="not-found-title">
        <span className="text-subtle font-mono text-xs">404</span>
        <h2 id="not-found-title">Return to the console</h2>
        <p>Choose a page from the sidebar or go to Metrics.</p>
        <Link to="/metrics" className={buttonVariants({ variant: "outline", className: "mt-4" })}>Go to Metrics</Link>
      </section>
    </>
  );
}
