export function PlaceholderPage({ title }: { title: string }) {
  return (
    <div className="flex flex-col gap-1">
      <h1 className="text-lg font-semibold">{title}</h1>
      <p className="text-sm text-muted-foreground">This page comes later.</p>
    </div>
  );
}
