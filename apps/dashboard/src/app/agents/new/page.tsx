"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useCreateAgent } from "@/hooks/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { AgentForm } from "@/components/agents/agent-form";

export default function NewAgentPage() {
  const router = useRouter();
  const create = useCreateAgent();

  return (
    <div className="p-6 space-y-4 max-w-3xl">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/agents" aria-label="Back">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <h1 className="text-2xl font-semibold">New agent</h1>
      </div>

      <Card>
        <CardContent className="pt-6">
          <AgentForm
            onSubmit={async (values) => {
              await create.mutateAsync({
                name: values.name,
                role: values.role,
                runtime_id: values.runtime_id,
                runtime_config: values.runtime_config,
                prompt_template: values.prompt_template,
              });
              router.push("/agents");
            }}
            isPending={create.isPending}
            submitError={create.error}
            submitLabel="Create agent"
          />
        </CardContent>
      </Card>
    </div>
  );
}
