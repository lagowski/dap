"use client";

/**
 * React Query hook factories (#778 Phase 3).
 *
 * Consolidates three patterns repeated across `hooks/api.ts` and
 * `hooks/api/runs.ts`:
 *
 * - `createEntityQuery` — the "query by nullable id" shape: disabled
 *   (with a stable per-hook noop key) until an id exists.
 * - `createInvalidatingMutation` — "mutate, then invalidate the sibling
 *   query keys", with keys optionally derived from the mutation variables.
 * - `refetchWhile` — the "poll while the entity is busy" refetchInterval
 *   callback (runs list/detail, workspace status).
 *
 * Mutations with bespoke success behaviour (auth cache flushes, the
 * approve-gate burst poll) intentionally stay hand-written.
 */

import {
  skipToken,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

export interface EntityQueryOptions {
  enabled?: boolean;
}

/**
 * Build a `useEntity(id, options?)` hook for a single-id resource.
 *
 * When `id` is null the query is disabled and keyed on
 * `[scope, "noop", ...suffix]` — a stable key per hook flavour so two
 * disabled hooks of the same scope don't share cache entries.
 */
export function createEntityQuery<T>(opts: {
  /** Cache scope, e.g. `"agents"` — first element of the noop key. */
  scope: string;
  /** Distinguishes sibling hooks' noop keys, e.g. `["usage"]`. */
  suffix?: readonly string[];
  queryKey: (id: string) => QueryKey;
  queryFn: (id: string) => Promise<T>;
}): (id: string | null, options?: EntityQueryOptions) => UseQueryResult<T> {
  const noopKey: QueryKey = [opts.scope, "noop", ...(opts.suffix ?? [])];
  return function useEntityQuery(id, options) {
    return useQuery({
      queryKey: id ? opts.queryKey(id) : noopKey,
      queryFn: id ? () => opts.queryFn(id) : skipToken,
      enabled: (options?.enabled ?? true) && id != null,
    });
  };
}

/**
 * Build a `useX()` mutation hook that invalidates `invalidates(vars)`
 * query keys on success.
 */
export function createInvalidatingMutation<TData, TVars>(opts: {
  mutationFn: (vars: TVars) => Promise<TData>;
  invalidates: (vars: TVars) => readonly QueryKey[];
}): () => UseMutationResult<TData, Error, TVars> {
  return function useInvalidatingMutation() {
    const qc = useQueryClient();
    return useMutation({
      mutationFn: opts.mutationFn,
      onSuccess: (_data, vars) => {
        for (const queryKey of opts.invalidates(vars)) {
          qc.invalidateQueries({ queryKey });
        }
      },
    });
  };
}

/**
 * `refetchInterval` callback: poll every `intervalMs` while the fetched
 * data reports busy (or hasn't arrived yet); stop once settled.
 */
export function refetchWhile<TData>(
  isBusy: (data: TData) => boolean,
  intervalMs: number,
): (query: { state: { data: TData | undefined } }) => number | false {
  return (query) => {
    const data = query.state.data;
    if (data && !isBusy(data)) return false;
    return intervalMs;
  };
}
