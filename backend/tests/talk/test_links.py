"""Links in an answer are found by code, from names in Bleep's own state (#391), and a link
an answer keeps is one to a URL Bleep watches (#543)."""

import pytest

from bleep.schemas.talk import IncidentLine, ServiceLine, Snapshot, TalkLink
from bleep.talk.links import known_links, links_in, watched_link

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


def test_a_name_inside_a_url_is_no_link_but_the_same_name_outside_one_is():
    assert links_in("checked https://shop.example/shop and HTTP://SHOP.local", [SHOP]) == []
    assert links_in("https://shop.example answers; shop is fine.", [SHOP]) == [SHOP]


WATCHED = ["https://dkwebsite.de", "http://shop.local:8080/health?full=1"]


@pytest.mark.parametrize(
    "link",
    [
        "https://dkwebsite.de",
        "https://dkwebsite.de/",
        "https://dkwebsite.de/blog/2026-11/post.html",
        "HTTPS://DKWEBSITE.DE/About",
        "http://shop.local:8080/health?full=1",
        "http://shop.local:8080/status",
    ],
)
def test_a_watched_url_or_a_plain_path_under_its_host_is_kept(link):
    assert watched_link(link, WATCHED)


@pytest.mark.parametrize(
    "link",
    [
        "https://dkwebsite.de.evil.com",
        "https://dkwebsite.de.evil.com/x",
        "https://evildkwebsite.de",
        "https://dkwebsite.dev",
        "https://evil.com/?u=https://dkwebsite.de",
        "https://dkwebsite.de@evil.com",
        "https://dkwebsite.de:443@evil.com",
        "https://user@dkwebsite.de",
        "https://dkwebsite.de\\@evil.com",
        "https://dkwebsite.de/\\evil.com",
        "https://dkwebsite.de/?next=https://evil.com",
        "https://dkwebsite.de/#evil",
        "https://dkwebsite.de:8443",
        "http://dkwebsite.de",
        "ftp://dkwebsite.de",
        "javascript://dkwebsite.de/%0aalert(1)",
        "dkwebsite.de/blog",
        "www.dkwebsite.de",
        "https://dkwеbsite.de",  # a Cyrillic "е"
        "https://xn--dkwbsite-3fg.de",
        "https://dkwebsite.de/blоg",  # a Cyrillic "о" in the path
        "http://shop.local:8080/health?full=2",
        "http://shop.local/health",
    ],
)
def test_any_other_link_and_every_look_alike_is_not_kept(link):
    assert not watched_link(link, WATCHED)


def test_with_no_url_checks_no_link_is_kept():
    assert not watched_link("https://dkwebsite.de", [])


def test_a_watched_url_with_a_user_keeps_only_itself():
    watched = ["https://user@dkwebsite.de/"]

    assert watched_link("https://user@dkwebsite.de/", watched)
    assert not watched_link("https://user@dkwebsite.de/x", watched)
