import { ServicePage } from "@/components/service/service-page";
import { serviceIdFrom } from "@/lib/routes";

export default async function Page({ params }: PageProps<"/services/[id]">) {
  const { id } = await params;
  const serviceId = serviceIdFrom(id);
  return <ServicePage key={serviceId} id={serviceId} />;
}
