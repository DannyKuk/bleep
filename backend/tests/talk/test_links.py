"""Links in an answer are found by code, from names in Bleep's own state (#391)."""

from bleep.schemas.talk import IncidentLine, ServiceLine, Snapshot, TalkLink
from bleep.talk.links import known_links, links_in

SHOP = TalkLink(kind="service", id="laptop/compose:shop", label="shop")
SHOP_14 = TalkLink(kind="incident", id="inc_14", label="shop #14")


def service(id: str, name: str, host: str = "laptop") -> ServiceLine:
    return ServiceLine(id=id, name=name, host=host, health="ok")


def test_the_longest_name_wins_and_a_name_inside_a_word_is_no_link():
    text = "shop #14 is open. shop-web and shopping are fine."

    assert links_in(text, [SHOP, SHOP_14]) == [SHOP_14]
    assert links_in("shop #140 is not known", [SHOP_14]) == []


def test_each_name_is_linked_once_in_the_order_it_first_stands():
    assert links_in("shop #14 is about shop. shop again.", [SHOP, SHOP_14]) == [SHOP_14, SHOP]


def test_a_name_the_model_wrote_differently_is_no_link():
    assert links_in("Shop is down", [SHOP]) == []


def test_a_name_with_regex_characters_matches_only_itself():
    odd = TalkLink(kind="service", id="a/compose:x", label="api (v2)")

    assert links_in("api (v2) is up; api v2 too", [odd]) == [odd]


def test_names_two_services_share_and_cut_ones_link_nothing():
    snapshot = Snapshot(
        now="2026-11-09 14:12",
        zone="UTC",
        services=[
            service("laptop/compose:web", "web"),
            service("m1/compose:web", "web", host="m1"),
            service("laptop/compose:shop", "shop"),
            service("laptop/compose:very-long…", "very-long…"),
        ],
        open_incidents=[
            IncidentLine(
                id="inc_14", name="shop #14", title="t", subject="s", status="open", opened="x"
            ),
            IncidentLine(id="inc_15", title="t", subject="s", status="open", opened="x"),
        ],
    )

    assert known_links(snapshot) == [SHOP, SHOP_14]
