import { ButtonItem, PanelSection, PanelSectionRow, staticClasses } from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { type ReactNode, useEffect, useState } from "react";
import { FaVolumeDown, FaVolumeMute, FaVolumeUp } from "react-icons/fa";

type ActionName = "volume_up" | "volume_down" | "mute";

interface CecStatus {
  ready: boolean;
  active: boolean;
  audioLogicalAddress: number | null;
  targetLabel: string | null;
  objectPath: string | null;
  warning: string | null;
  error: string | null;
}

interface CecActionResult {
  ok: boolean;
  action: ActionName;
  audioLogicalAddress?: number;
  objectPath?: string;
  error?: string;
}

const getStatus = callable<[], CecStatus>("get_status");
const volumeUp = callable<[], CecActionResult>("volume_up");
const volumeDown = callable<[], CecActionResult>("volume_down");
const mute = callable<[], CecActionResult>("mute");

const ACTIONS: Record<ActionName, () => Promise<CecActionResult>> = {
  volume_up: volumeUp,
  volume_down: volumeDown,
  mute,
};

const RPC_TIMEOUT_MS = 5000;

const withTimeout = async <T,>(promise: Promise<T>, message: string): Promise<T> => {
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_, reject) => {
        timeoutId = setTimeout(() => reject(new Error(message)), RPC_TIMEOUT_MS);
      }),
    ]);
  } finally {
    if (timeoutId !== undefined) {
      clearTimeout(timeoutId);
    }
  }
};

const ACTION_LABELS: Record<ActionName, ReactNode> = {
  volume_up: (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
      <FaVolumeUp />
      <span>Volume Up</span>
    </span>
  ),
  volume_down: (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
      <FaVolumeDown />
      <span>Volume Down</span>
    </span>
  ),
  mute: (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
      <FaVolumeMute />
      <span>Mute</span>
    </span>
  ),
};

function Content() {
  const [status, setStatus] = useState<CecStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [pending, setPending] = useState<Record<ActionName, boolean>>({
    volume_up: false,
    volume_down: false,
    mute: false,
  });

  const loadStatus = async () => {
    setStatusLoading(true);
    try {
      const nextStatus = await withTimeout(getStatus(), "Timed out while checking CEC status");
      setStatus(nextStatus);
    } catch (error) {
      console.error("Failed to load mote status", error);
      setStatus({
        ready: false,
        active: false,
        audioLogicalAddress: null,
        targetLabel: null,
        objectPath: null,
        warning: null,
        error: "Unable to reach the backend",
      });
    } finally {
      setStatusLoading(false);
    }
  };

  useEffect(() => {
    void loadStatus();
  }, []);

  const handleAction = async (action: ActionName) => {
    setPending((current) => ({ ...current, [action]: true }));
    try {
      const result = await withTimeout(ACTIONS[action](), `Timed out while running ${action}`);
      if (!result.ok) {
        toaster.toast({
          title: "mote",
          body: result.error ?? "CEC action failed",
        });
        await loadStatus();
      }
    } catch (error) {
      console.error(`Failed to execute ${action}`, error);
      toaster.toast({
        title: "mote",
        body: "Unable to reach the backend",
      });
      await loadStatus();
    } finally {
      setPending((current) => ({ ...current, [action]: false }));
    }
  };

  const ready = status?.ready ?? false;
  const statusLine = statusLoading
    ? "Checking CEC status..."
    : ready
      ? `CEC ready${status?.targetLabel ? ` · ${status.targetLabel}` : ""}`
      : status?.error ?? "CEC unavailable";

  return (
    <PanelSection title="mote">
      <PanelSectionRow>
        <div style={{ display: "flex", flexDirection: "column", gap: "4px", width: "100%" }}>
          <div>{statusLine}</div>
          {status?.warning ? <div>{status.warning}</div> : null}
          {!ready && !statusLoading ? (
            <ButtonItem layout="below" onClick={() => void loadStatus()}>
              Retry
            </ButtonItem>
          ) : null}
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={!ready || pending.volume_up}
          onClick={() => void handleAction("volume_up")}
        >
          {ACTION_LABELS.volume_up}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={!ready || pending.volume_down}
          onClick={() => void handleAction("volume_down")}
        >
          {ACTION_LABELS.volume_down}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={!ready || pending.mute}
          onClick={() => void handleAction("mute")}
        >
          {ACTION_LABELS.mute}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

export default definePlugin(() => {
  return {
    name: "mote",
    titleView: <div className={staticClasses.Title}>mote</div>,
    content: <Content />,
    icon: <FaVolumeUp />,
  };
});
