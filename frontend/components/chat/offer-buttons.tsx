"use client";

import { useT } from "@/lib/i18n";
import type { ChatConfirm } from "@/lib/talk-api";

export type Offer = ChatConfirm & {
  line?: string;
  pressing?: boolean;
  failed?: string;
  /** The incident a look's press opened or found open: the panel follows it. */
  looking?: string;
};

export function OfferButtons({
  offer,
  expired,
  onPress,
}: {
  offer: Offer;
  expired: boolean;
  onPress: (yes: boolean) => void;
}) {
  const t = useT().chat;
  if (offer.line || expired) {
    return <p className="pl-1 text-[13px] font-bold text-ink-2">{offer.line ?? t.expired}</p>;
  }
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex gap-2">
        <button
          type="button"
          disabled={offer.pressing}
          onClick={() => onPress(true)}
          className="h-9 rounded-full bg-accent px-4 text-[14px] font-bold text-accent-ink disabled:opacity-50"
        >
          {offer.yes_label}
        </button>
        <button
          type="button"
          disabled={offer.pressing}
          onClick={() => onPress(false)}
          className="h-9 rounded-full border border-line bg-surface px-4 text-[14px] font-bold disabled:opacity-50"
        >
          {offer.no_label}
        </button>
      </div>
      {offer.failed && (
        <p role="alert" className="pl-1 text-[13px] font-bold text-down">
          {offer.failed}
        </p>
      )}
    </div>
  );
}
