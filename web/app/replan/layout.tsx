/**
 * Chrome-free layout for the /replan recording page.
 * The page uses `fixed inset-0` to float over the root `<Header>` — this layout
 * is a passthrough so Next.js route grouping stays clean.
 */
export default function ReplanLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
