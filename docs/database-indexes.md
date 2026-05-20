# Database Indexes

This note records index decisions that are tied to API query shapes. It is
deliberately narrow: add entries when an endpoint depends on a specific access
path, or when an apparently obvious index is intentionally not added.

## Run Listing

`GET /runs` delegates to `dap_engine.persistence.runs.list_runs`. The repository
builds a count query and a paged select with the same predicates, then orders the
page by `runs.started_at DESC`.

Current filter surface:

| Filter | SQL shape | Index coverage |
| --- | --- | --- |
| none / admin all-runs | `ORDER BY started_at DESC` | `ix_runs_started_at` |
| date window | `started_at >= ?`, `started_at <= ?`, `ORDER BY started_at DESC` | `ix_runs_started_at` |
| project | `project_id = ?`, `ORDER BY started_at DESC` | `ix_runs_project_started` |
| ad-hoc project | `project_id IS NULL`, `ORDER BY started_at DESC` | `ix_runs_project_started` |
| pipeline | `pipeline_id = ?`, `ORDER BY started_at DESC` | `ix_runs_pipeline_started` |
| non-admin ownership | `user_id = ?`, `ORDER BY started_at DESC` | `ix_runs_user_id` |
| final status | `final_status IN (...)` | no dedicated index |

The ORM and in-code migrations currently define:

| Index | Columns | Migration |
| --- | --- | --- |
| `ix_runs_started_at` | `started_at` | `004_runs_index_started_at` |
| `ix_runs_project_started` | `project_id`, `started_at` | `005_runs_index_project_started` |
| `ix_runs_pipeline_started` | `pipeline_id`, `started_at` | `007_runs_index_pipeline_started` |
| `ix_runs_user_id` | `user_id` | `012_add_user_id_to_resources` |

No new index is added for `final_status`: run terminal state is low-cardinality
and is normally combined with ownership, project, pipeline, or date filters. A
single-column status index would add write cost without a clearly selective
access path.

No `(user_id, started_at)` composite is added yet. It is the likely next
candidate if non-admin default run listings become measurably slow, but the
current single-column ownership index already avoids a table scan and keeps the
schema aligned with the other ownership-backed resources. Add the composite only
with query-plan or production latency evidence, and cover it with both ORM
metadata and migration tests.
