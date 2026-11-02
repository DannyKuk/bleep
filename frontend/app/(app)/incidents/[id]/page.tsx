import { IncidentPage } from "@/components/incident/incident-page";

export default async function Page({ params }: PageProps<"/incidents/[id]">) {
  const { id } = await params;
  return <IncidentPage key={id} id={id} />;
}
