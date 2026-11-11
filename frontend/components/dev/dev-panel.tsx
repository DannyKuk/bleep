"use client";

import { useEffect, useState } from "react";

import { BudgetsForm } from "@/components/dev/budgets";
import { CostsPanel } from "@/components/dev/costs";
import { GuardLogPanel } from "@/components/dev/guard-log";
import { PromptsPanel } from "@/components/dev/prompts";
import { Failed, Loading, why } from "@/components/dev/status";
import { CodeText } from "@/components/code-text";
import { Panel } from "@/components/panel";
import { SaveLockProvider } from "@/components/save-lock";
import { ApiError, apiFetch } from "@/lib/api";
import type { DevSettingsView } from "@/lib/api-types";
import { useT } from "@/lib/i18n";

type State =
  { kind: "loading" } | { kind: "off" } | { kind: "failed"; why: string } | { kind: "on" };

/** The budgets, read from `GET /api/settings`. The model choice is on the settings page. */
function SettingsPanels() {
  const [view, setView] = useState<DevSettingsView | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const t = useT().dev;

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<DevSettingsView>("/api/settings", { signal: controller.signal })
      .then(setView)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setFailure(why(error));
      });
    return () => controller.abort();
  }, []);

  async function readAgain() {
    setView(await apiFetch<DevSettingsView>("/api/settings"));
  }

  const body = (form: (view: DevSettingsView) => React.ReactNode) =>
    view ? (
      form(view)
    ) : failure ? (
      <Failed>{t.settingsFailed(failure)}</Failed>
    ) : (
      <Loading>{t.loading}</Loading>
    );
  return (
    <SaveLockProvider>
      <Panel title={t.panels.budgets}>
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
  const t = useT().dev;

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

  const body = () => {
    if (state.kind === "loading") return <Loading>{t.loadingPanel}</Loading>;
    if (state.kind === "failed") return <Failed>{t.panelFailed(state.why)}</Failed>;
    if (state.kind === "off") {
      return (
        <p className="text-sm text-muted-foreground">
          <CodeText text={t.off} />
        </p>
      );
    }
    return (
      <div className="flex flex-col gap-4">
        <SettingsPanels />
        <Panel title={t.panels.prompts}>
          <PromptsPanel />
        </Panel>
        <Panel title={t.panels.cost}>
          <CostsPanel />
        </Panel>
        <Panel title={t.panels.guardLog}>
          <GuardLogPanel />
        </Panel>
      </div>
    );
  };
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">{t.title}</h1>
      {body()}
    </div>
  );
}
