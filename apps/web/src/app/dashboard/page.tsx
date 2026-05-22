import { headers } from "next/headers";

import DashboardClient from "./dashboard-client";

export const dynamic = "force-dynamic";

async function fetchSummary() {
  const headerStore = headers();
  const host = headerStore.get("x-forwarded-host") ?? headerStore.get("host");
  const protocol = headerStore.get("x-forwarded-proto") ?? "http";
  const base =
    process.env.NEXT_PUBLIC_BASE_URL ??
    (host === null ? "http://127.0.0.1:3000" : `${protocol}://${host}`);
  const res = await fetch(`${base}/api/v1/analytics/summary`, { cache: "no-store" });
  if (!res.ok) throw new Error(`summary fetch failed: ${res.status}`);
  return res.json();
}

export default async function DashboardPage() {
  const summary = await fetchSummary();
  return <DashboardClient initialSummary={summary} />;
}
