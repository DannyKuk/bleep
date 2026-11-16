import Link from "next/link";

import { linkHref, splitLinks, type TalkLink } from "@/lib/talk-api";

export function Answer({ text, links }: { text: string; links: TalkLink[] }) {
  return (
    <>
      {splitLinks(text, links).map((piece, index) =>
        typeof piece === "string" ? (
          piece
        ) : (
          <Link
            key={index}
            href={linkHref(piece.link)}
            className="font-bold text-accent underline-offset-2 hover:underline"
          >
            {piece.text}
          </Link>
        ),
      )}
    </>
  );
}
