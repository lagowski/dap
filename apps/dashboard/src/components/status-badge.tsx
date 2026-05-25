import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useGateCountdown } from "@/hooks/api/runs";
import { CheckCircle2, Loader2 } from "lucide-react";
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

export function RunStatusBadge({
  status,
  currentNode,
  pausedAtNode,
  gateExpiresAt,
  onApprove,
  approving,
}: {
  status: FinalStatus;
  currentNode?: string | null;
  pausedAtNode?: string | null;
  gateExpiresAt?: string | null;
  onApprove?: () => void;
  approving?: boolean;
}) {
  const countdown = useGateCountdown(status === "paused" ? gateExpiresAt : null);

  if (status === "running" && currentNode) {
    return (
      <Badge variant="info" className="gap-1.5">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span className="font-mono text-xs">{currentNode}</span>
      </Badge>
    );
  }

  if (status === "paused" && pausedAtNode) {
    return (
      <span className="inline-flex items-center gap-2">
        <Badge
          variant="warning"
          className={`gap-1.5 animate-pulse${countdown.isUrgent ? " border-red-400 bg-red-100 text-red-800 dark:border-red-600 dark:bg-red-950 dark:text-red-300" : ""}`}
        >
          <span className="font-mono text-xs">{pausedAtNode}</span>
          {countdown.label ? (
            <span className="text-xs">— expires in {countdown.label}</span>
          ) : (
            <span className="text-xs">— waiting</span>
          )}
        </Badge>
        {onApprove && (
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-xs"
            disabled={approving}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onApprove();
            }}
          >
            <CheckCircle2 className="mr-1 h-3 w-3" />
            Approve
          </Button>
        )}
      </span>
    );
  }

  return <Badge variant={RUN_VARIANT[status]}>{status}</Badge>;
}

export function NodeStatusBadge({ status }: { status: NodeStatus }) {
  return <Badge variant={NODE_VARIANT[status]}>{status}</Badge>;
}
