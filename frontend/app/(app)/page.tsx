import { Overview } from "@/components/overview/overview";
import { Recoveries } from "@/components/overview/recoveries";

export default function OverviewPage() {
  return (
    <div className="flex flex-col gap-10">
      <Overview />
      <Recoveries />
    </div>
  );
}
