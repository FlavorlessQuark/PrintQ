import { History, ChevronRight } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { getVerdict, getVerdictColor, getVerdictLabel, type PrintResult } from "@/lib/printq";

interface Props {
  items: PrintResult[];
  onOpen: (item: PrintResult) => void;
}

export function HistoryPanel({ items, onOpen }: Props) {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <History className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-sm font-semibold text-foreground">Print History</h3>
        <span className="ml-auto text-xs text-muted-foreground">{items.length}</span>
      </div>
      <ScrollArea className="flex-1">
        <ul className="divide-y divide-border">
          {items.length === 0 && (
            <li className="p-4 text-sm text-muted-foreground">No prints yet.</li>
          )}
          {items.map((item) => {
            const v = getVerdict(item.percentage);
            const color = getVerdictColor(v);
            const subline =
              v === "passed" ? "Unload completed" :
              v === "failed" ? "Unload blocked" :
              "Awaiting result";
            return (
              <li key={item.id}>
                <button
                  onClick={() => onOpen(item)}
                  className="group flex w-full cursor-pointer items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-accent/50"
                >
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{ backgroundColor: color, boxShadow: `0 0 8px ${color}` }}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-foreground">{item.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {item.timestamp || "--"} · {getVerdictLabel(v)} · {subline}
                    </p>
                  </div>
                  <span className="tabular-nums text-sm text-foreground/90">
                    {item.percentage != null ? `${Math.round(item.percentage)}%` : "--"}
                  </span>
                  <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-foreground" />
                </button>
              </li>
            );
          })}
        </ul>
      </ScrollArea>
    </div>
  );
}