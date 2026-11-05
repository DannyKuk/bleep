import { HostPage } from "@/components/host/host-page";
import { idFromSegment } from "@/lib/routes";

export default async function Page({ params }: PageProps<"/hosts/[id]">) {
  const { id } = await params;
  const hostId = idFromSegment(id);
  return <HostPage key={hostId} id={hostId} />;
}
