import { Card, CardContent } from "@/components/ui/card";
import { Markdown } from "@/components/ui/markdown";
import type { AgentCallableInfo } from "@/lib/api/types";

/**
 * "What this agent does" (#747). For a managed/python-func agent the prompt is
 * inert and the behaviour lives in the Python callable — so we show the
 * callable's own docstring (the author's description), or the resolution error
 * when it can't import on this engine. Render only for managed agents; pass the
 * fetched ``info`` (undefined while loading).
 */
export function CallableDoc({ info }: { info: AgentCallableInfo | undefined }) {
  return (
    <Card>
      <CardContent className="pt-6 space-y-2">
        <h2 className="text-sm font-medium">What this agent does</h2>
        {info === undefined ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : info.doc ? (
          <div className="text-foreground">
            <Markdown>{info.doc}</Markdown>
          </div>
        ) : !info.resolvable ? (
          <div className="space-y-1">
            <p className="text-sm text-destructive">
              The callable can&apos;t be imported on this engine, so its description is
              unavailable.
            </p>
            {info.error ? (
              <pre className="rounded border bg-destructive/10 p-2 text-xs whitespace-pre-wrap">
                {info.error}
              </pre>
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            The callable
            {info.callable_path ? (
              <>
                {" "}
                (<span className="font-mono">{info.callable_path}</span>)
              </>
            ) : null}{" "}
            has no docstring — ask its author to add one.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
