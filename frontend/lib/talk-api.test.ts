import { describe, expect, it } from "vitest";

import { linkHref, splitLinks, type TalkLink } from "@/lib/talk-api";

const SHOP: TalkLink = { kind: "service", id: "demo/compose:shop", label: "shop" };
const SHOP_14: TalkLink = { kind: "incident", id: "inc_14", label: "shop #14" };

describe("splitLinks", () => {
  it("links the longest name first and never a name inside a longer word", () => {
    expect(
      splitLinks("shop #14 waits; shop-web and shopping are fine; shop too.", [SHOP, SHOP_14]),
    ).toEqual([
      { text: "shop #14", link: SHOP_14 },
      " waits; shop-web and shopping are fine; ",
      { text: "shop", link: SHOP },
      " too.",
    ]);
  });

  it("leaves text without links as it is, regex characters included", () => {
    const odd: TalkLink = { kind: "service", id: "x", label: "api (v2)" };
    expect(splitLinks("all fine", [])).toEqual(["all fine"]);
    expect(splitLinks("api v2 and api (v2)", [odd])).toEqual([
      "api v2 and ",
      { text: "api (v2)", link: odd },
    ]);
  });
});

describe("linkHref", () => {
  it("builds in-app paths from ids, encoded", () => {
    expect(linkHref(SHOP)).toBe("/services/demo%2Fcompose%3Ashop");
    expect(linkHref({ kind: "incident", id: "a/b", label: "x" })).toBe("/incidents/a%2Fb");
  });
});
