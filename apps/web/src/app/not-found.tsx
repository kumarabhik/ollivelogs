import Link from "next/link";
import { Compass } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export default function NotFoundPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl items-center justify-center px-4 py-8">
      <Card className="w-full">
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="rounded-2xl bg-primary/15 p-3 text-primary">
              <Compass className="h-5 w-5" />
            </div>
            <div>
              <CardTitle>That route is not part of this console</CardTitle>
              <CardDescription>
                Head back to the chat workspace to keep testing streaming, cancel, and resume flows.
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <Link className={cn(buttonVariants())} href="/">
            Return home
          </Link>
        </CardContent>
      </Card>
    </main>
  );
}
