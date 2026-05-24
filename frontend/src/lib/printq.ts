export type Verdict = "awaiting" | "passed" | "failed";
export type ArmState = "idle" | "running" | "stopped";

export type PrintResult = {
  id: string;
  name: string;
  timestamp: string;
  percentage: number | null;
  info: string;
  image: string | null;
  source?: "socket" | "demo" | "awaiting" | "mock";
};

export function normalizeImageSrc(image: string | null | undefined): string | null {
  if (!image) return null;
  if (image.startsWith("data:")) return image;
  // Detect PNG vs JPEG from base64 magic bytes
  const mime = image.startsWith("iVBOR") ? "image/png" : "image/jpeg";
  return `data:${mime};base64,${image}`;
}

export function getVerdict(percentage: number | null | undefined, hasResult: boolean = true): Verdict {
  if (!hasResult) return "awaiting";
  if (percentage === null || percentage === undefined || Number.isNaN(percentage)) return "awaiting";
  if (percentage >= 80) return "passed";
  return "failed";
}

export function getVerdictLabel(v: Verdict): string {
  switch (v) {
    case "passed": return "Passed";
    case "failed": return "Failed";
    default: return "Awaiting Result";
  }
}

export function getVerdictColor(v: Verdict): string {
  switch (v) {
    case "passed": return "oklch(0.72 0.18 150)";
    case "failed": return "oklch(0.62 0.22 25)";
    default: return "oklch(0.55 0.04 250)";
  }
}

export type SourceLabel = "Awaiting" | "Demo Result" | "Backend status_update" | "Mock";
export function getSourceLabel(source: PrintResult["source"] | undefined): SourceLabel {
  if (source === "demo") return "Demo Result";
  if (source === "socket") return "Backend status_update";
  if (source === "mock") return "Mock";
  return "Awaiting";
}

export type Decision = "awaiting" | "pass" | "fail" | "unloading" | "clear";

export function getDecision(v: Verdict, armState: ArmState): Decision {
  if (armState === "running") return "unloading";
  if (armState === "stopped") return "clear";
  if (v === "passed") return "pass";
  if (v === "failed") return "fail";
  return "awaiting";
}

export function getDecisionContent(d: Decision): {
  title: string;
  subtitle: string;
  assistant: string;
  color: string;
} {
  switch (d) {
    case "pass":
      return {
        title: "PASS — UNLOAD ENABLED",
        subtitle: "Robot arm may clear the bed.",
        assistant: "Quality check passed. Unload is enabled.",
        color: "oklch(0.72 0.18 150)",
      };
    case "fail":
      return {
        title: "FAIL — UNLOAD BLOCKED",
        subtitle: "Queue paused to prevent automatic unloading.",
        assistant: "Quality check failed. Unload is blocked.",
        color: "oklch(0.62 0.22 25)",
      };
    case "unloading":
      return {
        title: "UNLOAD RUNNING",
        subtitle: "Robot arm is clearing the print bed.",
        assistant: "Arm is clearing the print bed.",
        color: "oklch(0.78 0.16 220)",
      };
    case "clear":
      return {
        title: "BED CLEAR",
        subtitle: "Ready for next print.",
        assistant: "Bed is clear. Next print can begin.",
        color: "oklch(0.78 0.16 200)",
      };
    default:
      return {
        title: "AWAITING RESULT",
        subtitle: "Listening for backend quality check.",
        assistant: "I'll update this panel when the quality check arrives.",
        color: "oklch(0.55 0.04 250)",
      };
  }
}

/** Verdict badge label used in the header (covers all decision states). */
export function getStateBadgeLabel(v: Verdict, armState: ArmState): string {
  if (armState === "running") return "Unloading";
  if (armState === "stopped") return "Bed Clear";
  return getVerdictLabel(v);
}

export function getStateBadgeColor(v: Verdict, armState: ArmState): string {
  if (armState === "running") return "oklch(0.78 0.16 220)";
  if (armState === "stopped") return "oklch(0.78 0.16 200)";
  return getVerdictColor(v);
}

export function canStartUnload(v: Verdict, armState: ArmState): boolean {
  return v === "passed" && armState !== "running";
}
export function canStopArm(armState: ArmState): boolean {
  return armState === "running";
}
export function canGoNextPrint(armState: ArmState): boolean {
  return armState === "stopped";
}

export const MOCK_HISTORY: PrintResult[] = [
  {
    id: "p08",
    name: "Print #08 — wall_hook_v2",
    timestamp: "10:42",
    percentage: 92,
    info: "Clean print. Layer adhesion looks consistent and edges are sharp. Unload completed.",
    image: null,
    source: "mock",
  },
  {
    id: "p07",
    name: "Print #07 — cable_clip_batch",
    timestamp: "10:18",
    percentage: 41,
    info: "Several clips show under-extrusion and one has a detached base. Unload blocked, queue paused.",
    image: null,
    source: "mock",
  },
  {
    id: "p06",
    name: "Print #06 — enclosure_lid_v1",
    timestamp: "09:51",
    percentage: 68,
    info: "Geometry is correct but visible stringing and a partial top-face defect. Unload blocked.",
    image: null,
    source: "mock",
  },
  {
    id: "p05",
    name: "Print #05 — phone_stand_v2",
    timestamp: "09:22",
    percentage: 87,
    info: "Structurally complete with minor surface artifacts near the top edge. Unload completed.",
    image: null,
    source: "mock",
  },
];

export const DEMO_INFO =
  "The print appears complete and matches the expected object profile. The detected object is contained within the target bounding box, and no major failure is visible.";

export function nowTime(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}