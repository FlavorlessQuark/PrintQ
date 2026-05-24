import {
  Bot,
  Camera,
  CheckCircle,
  Clock,
  Image as ImageIcon,
  Play,
  Square,
  XCircle,
  ChevronRight,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  getVerdict,
  getDecision,
  getDecisionContent,
  getSourceLabel,
  getStateBadgeLabel,
  getStateBadgeColor,
  canStartUnload,
  canStopArm,
  canGoNextPrint,
  normalizeImageSrc,
  type PrintResult,
  type ArmState,
} from "@/lib/printq";

interface Props {
  result: PrintResult | null;
  armState: ArmState;
  onStartArm: () => void;
  onStopArm: () => void;
  onNextPrint: () => void;
  onDemo: () => void;
}

export function PrintCard({ result, armState, onStartArm, onStopArm, onNextPrint, onDemo }: Props) {
  const hasResult = !!result;
  const verdict = getVerdict(result?.percentage ?? null, hasResult);
  const imgSrc = normalizeImageSrc(result?.image ?? null);
  const decision = getDecision(verdict, armState);
  const decisionContent = getDecisionContent(decision);
  const sourceLabel = hasResult ? getSourceLabel(result?.source) : "Awaiting";
  const stateLabel = getStateBadgeLabel(verdict, armState);
  const stripeColor = getStateBadgeColor(verdict, armState);

  const startEnabled = canStartUnload(verdict, armState);
  const stopEnabled = canStopArm(armState);
  const nextEnabled = canGoNextPrint(armState);

  const VerdictIcon =
    verdict === "passed" ? CheckCircle :
    verdict === "failed" ? XCircle :
    Clock;

  return (
    <div className="flex overflow-hidden rounded-xl border border-border bg-card shadow-lg">
      {/* Vertical status stripe */}
      <div
        className="w-1.5 shrink-0 transition-colors"
        style={{
          backgroundColor: stripeColor,
          boxShadow: `0 0 16px ${stripeColor}`,
        }}
        aria-label={`Status: ${stateLabel}`}
      />

      <div className="flex-1 p-5">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold text-foreground">
              {result?.name ?? "Awaiting print result"}
            </h2>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
              <span>Source: <span className="text-foreground/80">{sourceLabel}</span></span>
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                Last update: <span className="text-foreground/80">{result?.timestamp || "--"}</span>
              </span>
            </div>
          </div>
          <Badge
            className="gap-1 border-0 text-xs font-semibold text-white"
            style={{ backgroundColor: stripeColor }}
          >
            <VerdictIcon className="h-3.5 w-3.5" />
            {stateLabel}
          </Badge>
        </div>

        {/* Image */}
        <div className="mt-4 flex aspect-video items-center justify-center overflow-hidden rounded-md border border-border bg-background/50">
          {imgSrc ? (
            <img
              src={imgSrc}
              alt={result?.name ?? "Print result"}
              className="h-full w-full object-contain"
            />
          ) : (
            <div className="flex flex-col items-center gap-2 text-muted-foreground">
              <Camera className="h-10 w-10 opacity-70" />
              <p className="text-sm font-medium">Awaiting print result</p>
              <p className="text-xs">Listening for backend quality check</p>
            </div>
          )}
        </div>

        {/* Decision card */}
        <div
          className="mt-4 flex items-start gap-3 overflow-hidden rounded-lg border p-4"
          style={{
            borderColor: decisionContent.color,
            backgroundColor: `color-mix(in oklab, ${decisionContent.color} 12%, transparent)`,
            boxShadow: `0 0 24px -8px ${decisionContent.color}`,
          }}
        >
          <div
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full"
            style={{ backgroundColor: decisionContent.color }}
          >
            <Bot className="h-6 w-6 text-white" />
          </div>
          <div className="min-w-0 flex-1">
            <p
              className="text-lg font-bold leading-tight tracking-wide"
              style={{ color: decisionContent.color }}
            >
              {decisionContent.title}
            </p>
            <p className="text-sm text-foreground/80">{decisionContent.subtitle}</p>
            <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
              <Bot className="mt-0.5 h-3 w-3 shrink-0" style={{ color: decisionContent.color }} />
              <span><span className="font-medium text-foreground/80">PrintQ Assistant:</span> {decisionContent.assistant}</span>
            </p>
          </div>
        </div>

        {/* Model summary */}
        <div className="mt-4 rounded-md bg-muted/40 p-3">
          <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5" /> Model Summary
          </p>
          <p className="text-sm leading-relaxed text-foreground/90">
            {hasResult && result?.info
              ? result.info
              : "No analysis yet. Listening for the backend to send a print result."}
          </p>
        </div>

        {/* Status indicators */}
        <div className="mt-4 flex flex-wrap gap-2">
          <StatusChip
            ok={hasResult}
            label={hasResult ? "Result Received" : "Awaiting Result"}
            Icon={ImageIcon}
          />
          <StatusChip
            ok={hasResult && result?.percentage != null}
            label={hasResult && result?.percentage != null ? "Analysis Complete" : "Analysis Pending"}
            Icon={Sparkles}
          />
          {hasResult && (
            <StatusChip
              ok={verdict === "passed"}
              bad={verdict === "failed"}
              label={verdict === "passed" ? "Print Passed" : "Print Failed"}
              Icon={VerdictIcon}
            />
          )}
          <StatusChip
            ok={armState === "stopped" || armState === "running" || startEnabled}
            bad={hasResult && verdict === "failed"}
            warn={!hasResult}
            label={
              armState === "running" ? "Unloading" :
              armState === "stopped" ? "Bed Clear" :
              startEnabled ? "Unload Enabled" :
              hasResult && verdict === "failed" ? "Unload Blocked" :
              "Awaiting Result"
            }
            Icon={Bot}
          />
        </div>

        <Separator className="my-4" />

        {/* Actions */}
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={onStartArm}
            disabled={!startEnabled}
            className="gap-1.5"
            style={startEnabled ? { backgroundColor: "oklch(0.72 0.18 150)", color: "white" } : undefined}
          >
            <Play className="h-4 w-4" /> Start Unload
          </Button>
          <Button
            onClick={onStopArm}
            disabled={!stopEnabled}
            variant={stopEnabled ? "destructive" : "secondary"}
            className="gap-1.5"
          >
            <Square className="h-4 w-4" /> Stop Arm
          </Button>
          <Button
            onClick={onNextPrint}
            disabled={!nextEnabled}
            variant="secondary"
            className="gap-1.5"
          >
            <ChevronRight className="h-4 w-4" /> Next Print
          </Button>
          <Button onClick={onDemo} variant="outline" className="gap-1.5">
            <Sparkles className="h-4 w-4" /> Load Demo Result
          </Button>
        </div>
      </div>
    </div>
  );
}

function StatusChip({
  label,
  Icon,
  ok,
  warn,
  bad,
}: {
  label: string;
  Icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>;
  ok?: boolean;
  warn?: boolean;
  bad?: boolean;
}) {
  const color = bad
    ? "oklch(0.62 0.22 25)"
    : warn
    ? "oklch(0.78 0.16 80)"
    : ok
    ? "oklch(0.7 0.18 150)"
    : "oklch(0.5 0.01 265)";
  return (
    <div className="flex items-center gap-1.5 rounded-full border border-border bg-background/60 px-2.5 py-1 text-xs text-foreground/90">
      <Icon className="h-3.5 w-3.5" style={{ color }} />
      <span>{label}</span>
    </div>
  );
}