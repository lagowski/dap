import { Badge } from "@/components/ui/badge";
import type { FinalStatus, NodeStatus } from "@/lib/api/types";

const RUN_VARIANT: Record<FinalStatus, React.ComponentProps<typeof Badge>["variant"]> = {
  running: "info",
  success: "success",
  failed: "destructive",
  aborted: "warning",
  paused: "warning",
};

const NODE_VARIANT: Record<NodeStatus, React.ComponentProps<typeof Badge>["variant"]> = {
  pending: "outline",
  running: "info",
  success: "success",
  failed: "destructive",
  skipped: "warning",
};

export function RunStatusBadge({ status }: { status: FinalStatus }) {
  return <Badge variant={RUN_VARIANT[status]}>{status}</Badge>;
}

export function NodeStatusBadge({ status }: { status: NodeStatus }) {
  return <Badge variant={NODE_VARIANT[status]}>{status}</Badge>;
}
