export type VerificationStatus = "pending" | "approved" | "rejected";
export type FinalStatus = "running" | "success" | "failed" | "aborted" | "paused";

export interface PipelineState {
  run_id: string;
  repo: string;
  branch: string;
  commit_sha: string | null;

  available_issues: Record<string, unknown>[];
  selected_issue_ids: number[];

  tests_generated: boolean;
  test_files: string[];
  test_generation_errors: string[];

  max_attempts: number;
  attempt: number;
  tests_passed: boolean;
  last_test_output: string;

  modified_files: string[];
  implementation_notes: string | null;

  verification_status: VerificationStatus;
  verification_reason: string | null;

  final_status: FinalStatus;
}

export interface StateSnapshot {
  id: string;
  run_id: string;
  node_id: string;
  timestamp: string;
  state: PipelineState;
}
