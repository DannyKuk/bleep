"use client";

import { useEffect, useState } from "react";

import { BudgetsForm } from "@/components/dev/budgets";
import { CostsPanel } from "@/components/dev/costs";
import { GuardLogPanel } from "@/components/dev/guard-log";
import { ModelsForm } from "@/components/dev/models";
import { PromptsPanel } from "@/components/dev/prompts";
import { Failed, Loading, why } from "@/components/dev/status";
import { Panel } from "@/components/panel";
import { SaveLockProvider } from "@/components/save-lock";
import { ApiError, apiFetch } from "@/lib/api";
import type { DevSettingsView } from "@/lib/api-types";

type State =
  { kind: "loading" } | { kind: "off" } | { kind: "failed"; why: string } | { kind: "on" };

/** The model choice and the budgets, both read from one `GET /api/settings`, saved one at a
 * time, each from the view the other's save returned. */
function SettingsPanels() {
  const [view, setView] = useState<DevSettingsView | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<DevSettingsView>("/api/settings", { signal: controller.signal })
      .then(setView)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setFailure(`Could not load the settings: ${why(error)}`);
      });
    return () => controller.abort();
  }, []);

  async function readAgain() {
    setView(await apiFetch<DevSettingsView>("/api/settings"));
  }

  const body = (form: (view: DevSettingsView) => React.ReactNode) =>
    view ? form(view) : failure ? <Failed>{failure}</Failed> : <Loading>Loading</Loading>;
  return (
    <SaveLockProvider>
      <Panel title="Models">
        {body((v) => (
          <ModelsForm view={v} onSaved={setView} onStale={readAgain} />
        ))}
      </Panel>
      <Panel title="Budgets">
        {body((v) => (
          <BudgetsForm view={v} onSaved={setView} onStale={readAgain} />
        ))}
      </Panel>
    </SaveLockProvider>
  );
}

/** Developer settings, apart from the user UI; the backend answers only with `BLEEP_DEV_PANEL=true`. */
export function DevPanel() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<{ enabled: boolean }>("/api/dev", { signal: controller.signal })
      .then(() => setState({ kind: "on" }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 404) setState({ kind: "off" });
        else setState({ kind: "failed", why: why(error) });
      });
    return () => controller.abort();
  }, []);

  if (state.kind === "loading") return <Loading>Loading the dev panel</Loading>;
  if (state.kind === "failed") return <Failed>Could not load the dev panel: {state.why}</Failed>;
  if (state.kind === "off") {
    return (
      <p className="text-sm text-muted-foreground">
        The dev panel is off. Set <span className="font-mono">BLEEP_DEV_PANEL=true</span> in{" "}
        <span className="font-mono">.env</span> and restart Bleep to turn it on.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <SettingsPanels />
      <Panel title="Prompts">
        <PromptsPanel />
      </Panel>
      <Panel title="Cost">
        <CostsPanel />
      </Panel>
      <Panel title="Guard log">
        <GuardLogPanel />
      </Panel>
    </div>
  );
}
