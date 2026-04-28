"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useCreateProject } from "@/hooks/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ProjectForm } from "@/components/projects/project-form";

export default function NewProjectPage() {
  const router = useRouter();
  const create = useCreateProject();

  return (
    <div className="p-6 space-y-4 max-w-3xl">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/projects" aria-label="Back">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <h1 className="text-2xl font-semibold">New project</h1>
      </div>

      <Card>
        <CardContent className="pt-6">
          <ProjectForm
            onSubmit={async (values) => {
              const created = await create.mutateAsync({
                name: values.name,
                description: values.description,
                working_directory: values.working_directory,
                repo_url: values.repo_url,
                default_branch: values.default_branch,
                env_vars: values.env_vars,
                // pipelines stay empty — bind on the detail page
                // where the user can pick from real pipelines.
                pipelines: {},
              });
              router.push(`/projects/${created.id}`);
            }}
            isPending={create.isPending}
            submitError={create.error}
            submitLabel="Create project"
          />
        </CardContent>
      </Card>
    </div>
  );
}
