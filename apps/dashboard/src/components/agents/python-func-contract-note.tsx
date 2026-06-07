/**
 * "Not used by this runtime" note for the Inputs/Outputs pickers on a
 * python-func agent (#739). For python-func nodes these contracts are inert at
 * runtime — the node receives the full state directly (no prompt to scope, so
 * ``input_schema`` is ignored), and its returned dict is merged as-is
 * (``output_schema`` validation only applies to *text* output from LLM/CLI
 * agents). Surfacing this stops the pickers reading as load-bearing config —
 * the same fix as the inert prompt (#736).
 */
export function PythonFuncContractNote({ kind }: { kind: "inputs" | "outputs" }) {
  return (
    <p className="text-sm text-muted-foreground">
      Not used by this runtime. <span className="font-mono">python-func</span> nodes{" "}
      {kind === "inputs" ? (
        <>receive the full state directly — there&apos;s no prompt to scope</>
      ) : (
        <>
          return a dict that&apos;s merged as-is — <span className="font-mono">output_schema</span>{" "}
          validation only applies to text output from LLM/CLI agents
        </>
      )}
      , so this contract is documentary only (it can still feed Designer cohesion warnings).
    </p>
  );
}
