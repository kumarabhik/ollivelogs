import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <main className="mx-auto flex min-h-screen max-w-7xl flex-col gap-4 px-4 py-6 md:px-6 lg:py-8">
      <Skeleton className="h-20 w-full rounded-[28px]" />
      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Skeleton className="h-[72vh] w-full rounded-[28px]" />
        <Skeleton className="h-[72vh] w-full rounded-[28px]" />
      </div>
    </main>
  );
}
