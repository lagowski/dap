/** Map a server-side validation warning/error to an actionable fix hint.
 *
 * The engine emits terse, stable warning strings (e.g. "Node 'x' writes 'y'
 * but no downstream node reads it."). Operators kept asking *what to do* about
 * them, so we recognise the known classes here and return a concrete next step.
 * Returns `null` for messages we don't have advice for — callers should render
 * the raw message alone in that case. This is a pure, deterministic lookup (no
 * AI), so it's instant and never wrong about the engine's own semantics. */

const UNUSED_OUTPUT =
  /^Node '(?<node>.+)' writes '(?<field>.+)' but no downstream node reads it\.$/;

export function suggestionForWarning(message: string): string | null {
  const unused = UNUSED_OUTPUT.exec(message);
  if (unused?.groups) {
    const { node, field } = unused.groups;
    return (
      `Connect a node after “${node}” that reads “${field}”, ` +
      `remove “${field}” from ${node}’s output schema if it isn’t actually produced, ` +
      `or ignore this when “${field}” is a final output you read from the run’s state.`
    );
  }
  return null;
}

/** Distinct fix hints across a batch of messages, preserving first-seen order.
 *  Used to render shared advice once instead of repeating it per warning. */
export function distinctSuggestions(messages: readonly string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const message of messages) {
    const suggestion = suggestionForWarning(message);
    if (suggestion && !seen.has(suggestion)) {
      seen.add(suggestion);
      out.push(suggestion);
    }
  }
  return out;
}
