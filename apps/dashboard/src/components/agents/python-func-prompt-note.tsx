import Link from "next/link";

/**
 * Shared explainer for the inert ``prompt_template`` on ``python-func`` agents
 * (#736). The template is never rendered — the agent runs a Python callable,
 * not an LLM. The *actual* prompt such a callable sends is built at runtime and
 * recorded per-run under ``extensions.__audit``, so we point the reader to the
 * run-node detail where they can see it (it isn't editable from DAP — it lives
 * in the callable's own code/config). Used by both the agent detail card and
 * the edit-form field so the wording stays in one place.
 */
export function PythonFuncPromptNote({ callablePath }: { callablePath?: string | null }) {
  return (
    <p className="text-sm text-muted-foreground">
      Not used by this runtime. <span className="font-mono">python-func</span> agents run a Python
      callable
      {callablePath ? (
        <>
          {" "}
          (<span className="font-mono">{callablePath}</span>)
        </>
      ) : null}
      , not an LLM, so this template is never rendered — it&apos;s an inert,
      schema-required placeholder. The prompt the callable actually sends is built per-run and
      recorded under <span className="font-mono">extensions.__audit</span>; open a{" "}
      <Link href="/runs" className="text-blue-600 hover:underline dark:text-blue-400">
        recent run
      </Link>{" "}
      and click this node to see it.
    </p>
  );
}
