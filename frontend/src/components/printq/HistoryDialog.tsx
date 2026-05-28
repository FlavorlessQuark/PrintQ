import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Image as ImageIcon, Bot, Clock } from "lucide-react";
import { getVerdict, getVerdictColor, getVerdictLabel, getSourceLabel, normalizeImageSrc, type PrintResult } from "@/lib/printq";

interface Props {
  item: PrintResult | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function HistoryDialog({ item, open, onOpenChange }: Props) {
  if (!item) return null;
  const v = getVerdict(item.percentage);
  const color = getVerdictColor(v);
  const imgSrc = normalizeImageSrc(item.image);
  const armAllowed = v === "passed";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{item.name}</DialogTitle>
          <DialogDescription className="flex items-center gap-1.5 text-xs">
            <Clock className="h-3 w-3" /> {item.timestamp || "—"}
          </DialogDescription>
        </DialogHeader>

        <div className="flex aspect-video max-h-[420px] items-center justify-center overflow-hidden rounded-md border border-border bg-background/50">
          {imgSrc ? (
            <img src={imgSrc} alt={item.name} className="h-full w-full object-contain" />
          ) : (
            <div className="flex flex-col items-center gap-2 text-muted-foreground">
              <ImageIcon className="h-10 w-10" />
              <p className="text-xs">No image captured</p>
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="flex items-center justify-between rounded-md border border-border bg-muted/30 px-3 py-2">
            <span className="text-xs text-muted-foreground">Verdict</span>
            <Badge className="border-0 text-xs font-semibold text-white" style={{ backgroundColor: color }}>
              {getVerdictLabel(v)}
            </Badge>
          </div>
          <div className="flex items-center justify-between rounded-md border border-border bg-muted/30 px-3 py-2">
            <span className="text-xs text-muted-foreground">Percentage</span>
            <span className="tabular-nums font-medium">
              {item.percentage != null ? `${Math.round(item.percentage)}%` : "—"}
            </span>
          </div>
          <div className="flex items-center justify-between rounded-md border border-border bg-muted/30 px-3 py-2">
            <span className="text-xs text-muted-foreground">Source</span>
            <span className="text-xs text-foreground/90">{getSourceLabel(item.source)}</span>
          </div>
          <div className="flex items-center justify-between rounded-md border border-border bg-muted/30 px-3 py-2">
            <span className="text-xs text-muted-foreground">Timestamp</span>
            <span className="text-xs text-foreground/90">{item.timestamp || "—"}</span>
          </div>
        </div>

        <p className="text-sm leading-relaxed text-foreground/90">{item.info}</p>

        <div className="flex items-center gap-1.5 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs">
          <Bot className="h-3.5 w-3.5" style={{ color }} />
          {armAllowed ? "Unload enabled for this print." : "Unload blocked — queue paused for this print."}
        </div>
      </DialogContent>
    </Dialog>
  );
}