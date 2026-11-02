import { IncidentList } from "@/components/incident/incident-list";

export default function IncidentsPage() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Incidents</h1>
      <IncidentList />
    </div>
  );
}
