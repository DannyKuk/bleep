import { DevPanelLink } from "@/components/dev-panel-link";
import { SettingsPage } from "@/components/settings/settings-page";

export default function Page() {
  return (
    <div className="flex flex-col gap-4">
      <SettingsPage />
      <DevPanelLink />
    </div>
  );
}
