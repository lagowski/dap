import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

/**
 * ``false`` on the server and during hydration, ``true`` after (#917).
 *
 * Gate any output that depends on client-only state (react-query's
 * ``isFetching``, localStorage) behind it, so the first client render matches
 * the server HTML. A plain ``useEffect`` flag is not enough inside a Suspense
 * boundary: React hydrates the boundary after its parents, whose effects have
 * already run by then.
 */
export function useHasMounted(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}
