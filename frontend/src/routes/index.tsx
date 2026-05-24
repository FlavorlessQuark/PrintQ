import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { io, type Socket } from "socket.io-client";
import { Printer, Wifi, WifiOff, Sparkles, Bot } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { PrintCard } from "@/components/printq/PrintCard";
import { HistoryPanel } from "@/components/printq/HistoryPanel";
import { HistoryDialog } from "@/components/printq/HistoryDialog";
import {
  MOCK_HISTORY,
  DEMO_INFO,
  nowTime,
  type PrintResult,
  type ArmState,
} from "@/lib/printq";
import demoImage from "@/assets/demo-print.webp";

export const Route = createFileRoute("/")({
  component: Index,
});

type ConnState = "connecting" | "connected" | "mock" | "disconnected";

const BACKEND_URL =
  (import.meta.env.VITE_BACKEND_URL as string | undefined) ?? "http://localhost:5000";

/** Safely parse a status_update payload that may be a JSON string or an object. */
function parseStatusPayload(data: unknown): Partial<PrintResult> | null {
  try {
    if (typeof data === "string") return JSON.parse(data);
    if (data && typeof data === "object") return data as Partial<PrintResult>;
    return null;
  } catch (err) {
    console.warn("[PrintQ] Failed to parse status_update payload", err);
    return null;
  }
}

function Index() {
  const [current, setCurrent] = useState<PrintResult | null>(null);
  const [history, setHistory] = useState<PrintResult[]>(MOCK_HISTORY);
  const [armState, setArmState] = useState<ArmState>("idle");
  const [conn, setConn] = useState<ConnState>("connecting");
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [dialogItem, setDialogItem] = useState<PrintResult | null>(null);

  const socketRef = useRef<Socket | null>(null);
  const unloadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  
  // ─── Backend connection lives here ────────────────────────────────────
  useEffect(() => {
    let cancelled = false;

    // Health check
    fetch(`${BACKEND_URL}/api/health`)
      .then((r) => setHealthy(r.ok))
      .catch(() => setHealthy(false));

    // Socket.IO
    const socket = io(BACKEND_URL, {
      transports: ["websocket", "polling"],
      reconnectionAttempts: 3,
      timeout: 4000,
    });
    socketRef.current = socket;

    socket.on("connect", () => !cancelled && setConn("connected"));
    socket.on("disconnect", () => !cancelled && setConn("disconnected"));
    socket.on("connect_error", () => !cancelled && setConn("mock"));

    socket.on("status_update", (raw: unknown) => {
      const parsed = parseStatusPayload(raw);
      if (!parsed) return;
      const raw2 = parsed as Record<string, unknown>;
      const incoming: PrintResult = {
        id: `sock-${Date.now()}`,
        name: `Print — ${new Date().toLocaleTimeString()}`,
        timestamp: nowTime(),
        percentage: typeof raw2.success === "boolean" ? (raw2.success ? 100 : 0) : null,
        info: typeof raw2.desc === "string" ? raw2.desc : "",
        image: typeof raw2.image === "string" ? raw2.image : null,
        source: "socket",
      };
      setCurrent((prev) => {
        if (prev) setHistory((h) => [prev, ...h].slice(0, 20));
        return incoming;
      });
      // New print → re-lock arm until user starts again
      setArmState("idle");
    });

    return () => {
      cancelled = true;
      socket.removeAllListeners();
      socket.disconnect();
      if (unloadTimerRef.current) clearTimeout(unloadTimerRef.current);
    };
  }, []);

  const handleStartArm = () => {
    try {
      socketRef.current?.emit("start", { message: "start" });
    } catch (err) {
      console.warn("[PrintQ] start emit failed", err);
    }
    setArmState("running");
    // Simulate the unload completing after a couple seconds so the UI flows
    // through Unloading → Bed Clear even without backend confirmation.
    if (unloadTimerRef.current) clearTimeout(unloadTimerRef.current);
    unloadTimerRef.current = setTimeout(() => {
      setArmState((s) => (s === "running" ? "stopped" : s));
      setCurrent((c) =>
        c ? { ...c, info: `${c.info}${c.info ? " " : ""}Unload completed.` } : c,
      );
    }, 2500);
  };

  const handleStopArm = () => {
    if (unloadTimerRef.current) clearTimeout(unloadTimerRef.current);
    try {
      socketRef.current?.emit("stop", { message: "stop" });
    } catch (err) {
      console.warn("[PrintQ] stop emit failed", err);
    }
    // Return to the underlying verdict state rather than declaring bed clear.
    setArmState("idle");
  };

  const handleNextPrint = () => {
    if (current) setHistory((h) => [current, ...h].slice(0, 20));
    setCurrent(null);
    setArmState("idle");
  };

  const handleDemo = () => {
    if (unloadTimerRef.current) clearTimeout(unloadTimerRef.current);
    setCurrent({
      id: `demo-${Date.now()}`,
      name: "Demo Print — phone_stand_v3",
      timestamp: nowTime(),
      percentage: 86,
      info: DEMO_INFO,
      image: demoImage,
      source: "demo",
    });
    setArmState("idle");
  };

  const connMeta = useMemo(() => {
    switch (conn) {
      case "connected": return { label: "Backend: Connected", color: "oklch(0.7 0.18 150)", Icon: Wifi };
      case "mock": return { label: "Mock Mode Active", color: "oklch(0.78 0.16 80)", Icon: Sparkles };
      case "disconnected": return { label: "Backend: Offline", color: "oklch(0.62 0.22 25)", Icon: WifiOff };
      default: return { label: "Connecting…", color: "oklch(0.55 0.04 250)", Icon: Wifi };
    }
  }, [conn]);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-6xl px-4 py-6 sm:py-8">
        {/* Header */}
        <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
              <span
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-primary/40 bg-primary/10"
                style={{ boxShadow: "0 0 18px -4px oklch(0.78 0.16 220)" }}
              >
                <Printer className="h-5 w-5 text-primary" />
              </span>
              PrintQ
              <span
                className="ml-1 flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-primary"
                title="PrintQ Assistant"
              >
                <Bot className="h-3 w-3" /> Assistant
              </span>
            </h1>
            <p className="text-sm text-muted-foreground">
              Vision-checked print clearing for a robot arm
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Badge
              variant="outline"
              className="gap-1.5 border-border"
              style={{ color: connMeta.color }}
              title={healthy === null ? undefined : healthy ? "Health check OK" : "Health check failed"}
            >
              <connMeta.Icon className="h-3.5 w-3.5" />
              {connMeta.label}
            </Badge>
          </div>
        </header>

        {/* Main */}
        <main className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_320px]">
          <PrintCard
            result={current}
            armState={armState}
            onStartArm={handleStartArm}
            onStopArm={handleStopArm}
            onNextPrint={handleNextPrint}
            onDemo={handleDemo}
          />
          <HistoryPanel items={history} onOpen={setDialogItem} />
        </main>
      </div>

      <HistoryDialog
        item={dialogItem}
        open={!!dialogItem}
        onOpenChange={(o) => !o && setDialogItem(null)}
      />
    </div>
  );
}
