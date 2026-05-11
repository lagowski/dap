"use client";

import { use } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useProject, useUpdateProject } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ProjectForm } from "@/components/projects/project-form";

export default function EditProjectPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: project, isPending, isError, error } = useProject(id);
  const update = useUpdateProject();

  if (isPending) {
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  }
  if (isError) {
    return (
      <div className="p-6">
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">
            {formatApiError(error)}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4 max-w-3xl">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/projects/${id}`} aria-label="Back">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <h1 className="text-2xl font-semibold">Edit {project.name}</h1>
      </div>

      <Card>
        <CardContent className="pt-6">
          <ProjectForm
            initialValues={{
              name: project.name,
              description: project.description,
              working_directory: project.working_directory,
              repo_url: project.repo_url,
              default_branch: project.default_branch,
              env_vars: project.env_vars,
            }}
            onSubmit={async (values) => {
              await update.mutateAsync({
                id,
                payload: {
                  name: values.name,
                  description: values.description,
                  working_directory: values.working_directory,
                  repo_url: values.repo_url,
                  default_branch: values.default_branch,
                  env_vars: values.env_vars,
                  // Bindings are managed on the detail page; preserve
                  // them through the update so a metadata edit doesn't
                  // wipe the user's workflow wiring.
                  pipelines: project.pipelines,
                },
              });
              router.push(`/projects/${id}`);
            }}
            isPending={update.isPending}
            submitError={update.error}
            submitLabel="Save"
            cancelHref={`/projects/${id}`}
          />
        </CardContent>
      </Card>
    </div>
  );
}
