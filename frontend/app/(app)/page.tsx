import { Overview } from "@/components/overview/overview";
import { Recoveries } from "@/components/overview/recoveries";

export default function OverviewPage() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Overview</h1>
      <Overview />
      <Recoveries />
    </div>
  );
}
