import { DevPanel } from "@/components/dev/dev-panel";

export default function DevPage() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Dev</h1>
      <DevPanel />
    </div>
  );
}
