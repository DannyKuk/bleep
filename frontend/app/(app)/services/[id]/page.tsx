import { ServicePage } from "@/components/service/service-page";
import { idFromSegment } from "@/lib/routes";

export default async function Page({ params }: PageProps<"/services/[id]">) {
  const { id } = await params;
  const serviceId = idFromSegment(id);
  return <ServicePage key={serviceId} id={serviceId} />;
}
