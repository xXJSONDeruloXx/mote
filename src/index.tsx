import {
  ButtonItem,
  Field,
  PanelSection,
  PanelSectionRow,
  Spinner,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaWrench } from "react-icons/fa";

type ActionName = "volume_up" | "volume_down" | "mute";
type SetupAction = "verify" | "install" | "uninstall";
type SetupState = "configured" | "needs_setup" | "needs_repair" | "error";

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

interface SleepWakeDetails {
  stateFile: string | null;
  cecPhysicalAddress: string | null;
  cecDevice: string | null;
  cecObjectPath: string | null;
  bluetoothTarget: string | null;
  bluetoothHelper: string | null;
  keepListPath: string | null;
  persistentLayout: string | null;
}

interface SleepWakeStatus {
  ok: boolean;
  action: SetupAction;
  state: SetupState;
  summary: string;
  warnings: string[];
  failures: string[];
  details: SleepWakeDetails;
  stdout: string;
  stderr: string;
  returncode: number;
}

const getStatus = callable<[], CecStatus>("get_status");
const volumeUp = callable<[], CecActionResult>("volume_up");
const volumeDown = callable<[], CecActionResult>("volume_down");
const mute = callable<[], CecActionResult>("mute");
const getSleepWakeStatus = callable<[], SleepWakeStatus>("get_sleep_wake_status");
const installSleepWake = callable<[], SleepWakeStatus>("install_sleep_wake");
const uninstallSleepWake = callable<[], SleepWakeStatus>("uninstall_sleep_wake");

const ACTIONS: Record<ActionName, () => Promise<CecActionResult>> = {
  volume_up: volumeUp,
  volume_down: volumeDown,
  mute,
};

const SETUP_ACTIONS: Record<Exclude<SetupAction, "verify">, () => Promise<SleepWakeStatus>> = {
  install: installSleepWake,
  uninstall: uninstallSleepWake,
};

const RPC_TIMEOUT_MS = 5000;
const SETUP_TIMEOUT_MS = 120000;

const withTimeout = async <T,>(promise: Promise<T>, message: string, timeoutMs = RPC_TIMEOUT_MS): Promise<T> => {
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_, reject) => {
        timeoutId = setTimeout(() => reject(new Error(message)), timeoutMs);
      }),
    ]);
  } finally {
    if (timeoutId !== undefined) {
      clearTimeout(timeoutId);
    }
  }
};

const ACTION_LABELS: Record<ActionName, string> = {
  volume_up: "Volume Up",
  volume_down: "Volume Down",
  mute: "Mute",
};

function setupStateLabel(state: SetupState): string {
  switch (state) {
    case "configured":
      return "Configured";
    case "needs_setup":
      return "Needs setup";
    case "needs_repair":
      return "Needs repair";
    default:
      return "Error";
  }
}

function setupPrimaryActionLabel(status: SleepWakeStatus | null): string {
  if (!status) {
    return "Set Up";
  }
  switch (status.state) {
    case "configured":
      return "Reinstall";
    default:
      return "Set Up";
  }
}

function shouldDisplaySetupWarning(warning: string): boolean {
  return ![
    "Optional MediaTek rule not installed",
    "/usr/lib/holo/holo-sync-var not available; skipped SteamOS update dry-run",
  ].some((hiddenWarning) => warning.includes(hiddenWarning));
}

function Content() {
  const [status, setStatus] = useState<CecStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [setupStatus, setSetupStatus] = useState<SleepWakeStatus | null>(null);
  const [setupLoading, setSetupLoading] = useState(true);
  const [pending, setPending] = useState<Record<ActionName, boolean>>({
    volume_up: false,
    volume_down: false,
    mute: false,
  });
  const [setupPending, setSetupPending] = useState<SetupAction | null>(null);

  const loadStatus = async () => {
    setStatusLoading(true);
    try {
      const nextStatus = await withTimeout(getStatus(), "Timed out while checking CEC status");
      setStatus(nextStatus);
    } catch (error) {
      console.error("Failed to load cec-mote status", error);
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

  const loadSleepWakeStatus = async () => {
    setSetupLoading(true);
    try {
      const nextStatus = await withTimeout(
        getSleepWakeStatus(),
        "Timed out while checking sleep/wake setup",
        SETUP_TIMEOUT_MS,
      );
      setSetupStatus(nextStatus);
    } catch (error) {
      console.error("Failed to load sleep/wake setup status", error);
      setSetupStatus({
        ok: false,
        action: "verify",
        state: "error",
        summary: "Unable to reach the setup backend.",
        warnings: [],
        failures: ["Backend request failed"],
        details: {
          stateFile: null,
          cecPhysicalAddress: null,
          cecDevice: null,
          cecObjectPath: null,
          bluetoothTarget: null,
          bluetoothHelper: null,
          keepListPath: null,
          persistentLayout: null,
        },
        stdout: "",
        stderr: "",
        returncode: 1,
      });
    } finally {
      setSetupLoading(false);
    }
  };

  useEffect(() => {
    void loadStatus();
    void loadSleepWakeStatus();
  }, []);

  const handleAction = async (action: ActionName) => {
    setPending((current) => ({ ...current, [action]: true }));
    try {
      const result = await withTimeout(ACTIONS[action](), `Timed out while running ${action}`);
      if (!result.ok) {
        toaster.toast({
          title: "cec-mote",
          body: result.error ?? "CEC action failed",
        });
        await loadStatus();
      }
    } catch (error) {
      console.error(`Failed to execute ${action}`, error);
      toaster.toast({
        title: "cec-mote",
        body: "Unable to reach the backend",
      });
      await loadStatus();
    } finally {
      setPending((current) => ({ ...current, [action]: false }));
    }
  };

  const handleSetupAction = async (action: Exclude<SetupAction, "verify">) => {
    setSetupPending(action);
    try {
      const result = await withTimeout(
        SETUP_ACTIONS[action](),
        `Timed out while running ${action}`,
        SETUP_TIMEOUT_MS,
      );
      setSetupStatus(result);
      toaster.toast({
        title: "cec-mote",
        body: result.summary,
      });
    } catch (error) {
      console.error(`Failed to execute ${action}`, error);
      toaster.toast({
        title: "cec-mote",
        body: "Unable to reach the setup backend",
      });
    } finally {
      setSetupPending(null);
      await loadSleepWakeStatus();
    }
  };

  const ready = status?.ready ?? false;

  const setupStatusLabel = setupLoading
    ? "Checking sleep/wake setup..."
    : setupStatus
      ? setupStateLabel(setupStatus.state)
      : "Status unavailable";

  const visibleSetupWarnings = setupStatus?.warnings.filter(shouldDisplaySetupWarning) ?? [];

  return (
    <>
      <PanelSection>
        {!ready && !statusLoading ? (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => void loadStatus()}>
              Retry CEC Status
            </ButtonItem>
          </PanelSectionRow>
        ) : null}
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

      <PanelSection title="Sleep / wake setup">
        <PanelSectionRow>
          <Field
            focusable
            highlightOnFocus
            label="Setup status"
            description={setupStatus?.summary ?? "Install or repair CEC TV sleep/wake and Bluetooth wake support."}
          >
            {setupLoading ? <Spinner /> : setupStatusLabel}
          </Field>
        </PanelSectionRow>

        {setupStatus?.details.persistentLayout ? (
          <PanelSectionRow>
            <Field focusable highlightOnFocus label="Persistent layout">
              {setupStatus.details.persistentLayout}
            </Field>
          </PanelSectionRow>
        ) : null}

        {setupStatus?.details.cecDevice ? (
          <PanelSectionRow>
            <Field focusable highlightOnFocus label="CEC device">
              {setupStatus.details.cecDevice}
            </Field>
          </PanelSectionRow>
        ) : null}

        {setupStatus?.details.cecPhysicalAddress ? (
          <PanelSectionRow>
            <Field focusable highlightOnFocus label="CEC physical address">
              {setupStatus.details.cecPhysicalAddress}
            </Field>
          </PanelSectionRow>
        ) : null}

        {setupStatus?.details.bluetoothTarget ? (
          <PanelSectionRow>
            <Field focusable highlightOnFocus label="Bluetooth wake target">
              {setupStatus.details.bluetoothTarget}
            </Field>
          </PanelSectionRow>
        ) : null}

        {visibleSetupWarnings.slice(0, 3).map((warning) => (
          <PanelSectionRow key={warning}>
            <Field focusable highlightOnFocus label="Warning" description={warning} />
          </PanelSectionRow>
        ))}

        {setupStatus?.failures.slice(0, 3).map((failure) => (
          <PanelSectionRow key={failure}>
            <Field focusable highlightOnFocus label="Issue" description={failure} />
          </PanelSectionRow>
        ))}

        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={setupPending !== null}
            onClick={() => void handleSetupAction("install")}
          >
            {setupPending === "install" ? "Working..." : setupPrimaryActionLabel(setupStatus)}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={setupPending !== null}
            onClick={() => void loadSleepWakeStatus()}
          >
            {setupPending === "verify" ? "Working..." : "Refresh Setup Status"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={setupPending !== null || setupStatus?.state === "needs_setup"}
            onClick={() => void handleSetupAction("uninstall")}
          >
            {setupPending === "uninstall" ? "Working..." : "Uninstall Setup"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  return {
    name: "cec-mote",
    titleView: <div className={staticClasses.Title}>cec-mote</div>,
    content: <Content />,
    icon: <FaWrench />,
  };
});
