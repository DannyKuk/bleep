import random
import re

import pytest

from bleep.guard.redact import redact_env

GITHUB = "ghp_" + "A1b2" * 9
HEX40 = "0123456789abcdef" * 2 + "01234567"


def shown(value: str) -> str:
    return redact_env([f"URL={value}"])[0].removeprefix("URL=")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("postgresql://shop:shop@db:5432/shop", "postgresql://<redacted>@db:5432/<redacted>"),
        ("redis://:hunter2@cache:6379/0", "redis://<redacted>@cache:6379/<redacted>"),
        ("redis://:hunter2@cache:6379", "redis://<redacted>@cache:6379"),
        (
            "https://4f2a9c0d1e@o450123.ingest.sentry.io/5123456",
            "https://<redacted>@o450123.ingest.sentry.io/<redacted>",
        ),
        (
            "https://hooks.slack.com/services/T0123ABCD/B0123ABCD/xoxSecretPart1234",
            "https://hooks.slack.com/<redacted>",
        ),
        (
            "https://bucket.s3.amazonaws.com/backup.tar?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20261109&X-Amz-Signature=9f8e7d6c",
            "https://bucket.s3.amazonaws.com/<redacted>?<redacted>",
        ),
        ("https://api.example.com?sig=abc", "https://api.example.com?<redacted>"),
        ("https://app.example.com/#access_token=abc", "https://app.example.com/#<redacted>"),
        ("https://h.example.com/p?q=1#f", "https://h.example.com/<redacted>?<redacted>#<redacted>"),
        ("http://[::1]:8080/health", "http://[::1]:8080/<redacted>"),
        ("postgres://u:pw@[2001:db8::5]/db", "postgres://<redacted>@[2001:db8::5]/<redacted>"),
        ("http://[::ffff:10.0.0.1]", "http://[::ffff:10.0.0.1]"),
        ("http://10.0.0.7:9000", "http://10.0.0.7:9000"),
        ("http://shop-db:5432", "http://shop-db:5432"),
        ("http://shop_db_1", "http://shop_db_1"),
        ("https://example.com/", "https://example.com/"),
        ("postgresql+asyncpg://u:p@db/x", "postgresql+asyncpg://<redacted>@db/<redacted>"),
        (
            "mongodb+srv://u:p@cluster0.ab1cd.mongodb.net/x",
            "mongodb+srv://<redacted>@cluster0.ab1cd.mongodb.net/<redacted>",
        ),
        ("HTTPS://Example.COM", "HTTPS://Example.COM"),
        ("postgres://shop@shop-db:5432/shop", "postgres://<redacted>@shop-db:5432/<redacted>"),
        ("postgres://u:p%40ss@db:5432/x", "postgres://<redacted>@db:5432/<redacted>"),
        ("postgres://u%40x:p%3A%2F@db/x", "postgres://<redacted>@db/<redacted>"),
        ("http://@db", "http://<redacted>@db"),
        (f"https://{GITHUB}@github.com/acme/app.git", "https://<redacted>@github.com/<redacted>"),
        ("http://db:0080", "http://db:0080"),
    ],
)
def test_a_url_keeps_its_scheme_host_and_port(value, expected):
    assert shown(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "hunter2",
        "",
        "db:5432",
        "shop-db",
        "mailto:ops@example.com",
        "redis:hunter2",
        "jdbc:postgresql://db:5432/shop",
        "//db:5432/shop",
        "file:///etc/passwd",
        "postgresql://",
        "postgresql:///shop?host=/run/postgresql",
        "http://user:pw@",
        "http://:8080",
        "http:/db",
        "http:db",
        "http:///db",
    ],
)
def test_a_value_that_is_not_a_url_with_a_host_is_hidden_whole(value):
    assert shown(value) == "<redacted>"


@pytest.mark.parametrize(
    "value",
    [
        # userinfo the readings disagree on: the password holds `@`, `/`, `?` or `#`
        "postgres://u:p@ss@db/x",
        "postgres://u:SECRET/x@db/x",
        "postgres://u:1234/SECRET@db:5432/x",
        "postgres://u:12#SECRET@db",
        "postgres://u:12?SECRET@db",
        "postgres://SECRET@a@db",
        "https://api.example.com/?token=abc@SECRET",
        "https://api.example.com/p@SECRET",
        "http://SECRET.com#@good.com",
        # backslashes: browsers read them as `/`, urllib does not
        "http://SECRET.com\\@good.com",
        "http://good.com\\SECRET",
        "http://u:SECRET\\@db",
        # whitespace, control bytes and anything outside printable ASCII
        " https://SECRET.example.com",
        "https://SECRET.example.com ",
        "https://SECR\nET.example.com",
        "https://SECR\tET.example.com",
        "https://SECR\x00ET.example.com",
        "https://SECR\x1b[31mET.example.com",
        "https://ＳECRET.example.com",
        "https://SECRET.example.com:８０",
        "https://SECRET​.example.com",
        # hosts and ports outside the allowlist
        "https://SECRET%40x.example.com",
        "https://exa%6dple.com/SECRET",
        "https://SECRET;x.example.com",
        "https://SECRET,db2:5432",
        "mongodb://u:p@SECRET:27017,db2:27017/x",
        "https://SECRET=1.example.com",
        "https://SECRET.example.com:",
        "https://SECRET.example.com:80x",
        "https://SECRET.example.com:99999",
        "https://SECRET.example.com:-1",
        "https://SECRET.example.com:+80",
        "https://SECRET.example.com:80:90",
        "https://-SECRET.example.com",
        "https://SECRET-.example.com",
        "https://SECRET..example.com",
        "https://SECRET.example.com.",
        "https://" + "SECRET" * 11 + ".example.com",
        "https://" + ".".join(["SECRET" + "a" * 50] * 5),
        "https://<untrusted>SECRET</untrusted>",
        "http://[SECRET]",
        "http://[::1%25SECRET]",
        "http://[::1]SECRET",
        "http://[::1]:SECRET",
        "http://[::1",
        "http://SECRET]",
        # odd schemes
        "ht~tp://SECRET.example.com",
        "1http://SECRET.example.com",
        "h ttp://SECRET.example.com",
        "<untrusted>://SECRET.example.com",
        "a" * 33 + "://SECRET.example.com",
        # hosts that the secret patterns recognise
        f"https://{GITHUB}SECRET.example.com",
        "https://AKIAIOSFODNN7SECRETX.example.com",
    ],
)
def test_a_value_the_readings_disagree_on_is_hidden_whole(value):
    out = redact_env([f"URL={value}"])
    assert out == ["URL=<redacted>"]
    assert "SECRET" not in out[0]


@pytest.mark.parametrize(
    "value",
    [
        "pushbullet://o.gn5kj6nfhv736I7jC3cj3QLRiyhgl98b",
        "ifttt://bL7xQ2ab9TzK1mPq3RwYv8/?events=event1",
        "pushover://shoutrrr:apptoken42@uQiRzpo4DXghDmr9QzzfQu27cmVRsG/",
        "pover://userkey42@azGDORePK8gMaC0QOYAMyEEuzJnyUi",
        "prowl://4f2a9c0d1e4f2a9c0d1e4f2a9c0d1e4f2a9c0d1e",
        "opsgenie://3a7f9c2e-1b4d-4e8a-9f6c-2d5b8e1a7c3f",
        "fcm://myproj@AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI",
        "splunk://routingkey@a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
        "pagerduty://integrationkey@apikey1234567890abcdefghij",
        "join://abcdef0123456789abcdef0123456789/device",
        "serverchan://SCT123456Tabcdefghijklmnop",
        "wecombot://3a7f9c2e-1b4d-4e8a-9f6c-2d5b8e1a7c3f",
        "ntfy://my-private-topic",
        "ftp://u:p@files.example.com/x",
        "ssh://git@github.com",
        "tcp://db:5432",
        "s3://bucket/key",
        "postgresql+psycopg@x://db",
        "https://k3j4h5g6f7d8s9a0q1w2e3r4t5y6u7i8.lambda-url.us-east-1.on.aws/",
        "https://sk_live_4eC39HqLyjWDarjtT1zdp7dc.example.com",
        "https://glpat-9aBcD3fGh1JkLmN0pQrS.example.com",
        "https://npm_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8.example.com",
        "https://AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI.example.com",
        "https://3a7f9c2e-1b4d-4e8a-9f6c-2d5b8e1a7c3f.example.com",
        "https://hooks.3A7F9C2E-1B4D-4E8A-9F6C-2D5B8E1A7C3F.example.com:443",
        f"https://{HEX40}.example.com/x",
        "https://sk_live_abcdefghijklMNOPqrstuvwx.example.com",
    ],
)
def test_a_token_where_the_host_goes_is_hidden_whole(value):
    assert redact_env([f"URL={value}"]) == ["URL=<redacted>"]


@pytest.mark.parametrize(
    "host",
    [
        "db",
        "shop-db",
        "postgres.internal",
        "10.0.0.5",
        "[::1]",
        "3f2a9c0d1e4b",
        "myproject_db_1",
        "cluster0.ab1cd.mongodb.net",
        "mydb.c9akciq32.us-east-1.rds.amazonaws.com",
        "db.abcdefghijklmnopqrst.supabase.co",
        "ep-cool-darkness-123456.us-east-2.aws.neon.tech",
        "o450123.ingest.sentry.io",
        "SHOP-DATABASE-PRIMARY.INTERNAL",
    ],
)
def test_a_real_service_host_is_shown(host):
    assert shown(f"postgresql+psycopg://u:p@{host}:5432/x") == (
        f"postgresql+psycopg://<redacted>@{host}:5432/<redacted>"
    )


def test_a_password_written_where_the_host_and_port_go_is_shown_as_a_known_limit():
    """With no `@`, every client reads these as host and port, and so does Bleep; nothing tells
    them apart from a real host and port (guard.md, Known limits)."""
    assert shown("postgres://admin:31337") == "postgres://admin:31337"
    assert shown("redis://hunter2:6379") == "redis://hunter2:6379"


def test_hosts_that_secret_patterns_recognise_are_hidden():
    assert shown(f"https://{GITHUB}.example.com") == "<redacted>"
    assert shown("https://AKIAIOSFODNN7EXAMPLE.example.com") == "<redacted>"


def test_a_url_under_a_secret_key_is_hidden_whole():
    assert redact_env(
        [
            "REDIS_PASSWORD=redis://hunter2:6379",
            "API_TOKEN=https://api.example.com/v1",
            "SECRET_KEY=http://abc123:80",
            "SENTRY_DSN=https://k@o1.ingest.sentry.io/2",
            "DB_PASSWORD_URL=postgres://u:p@db/x",
        ]
    ) == [
        "REDIS_PASSWORD=<redacted>",
        "API_TOKEN=<redacted>",
        "SECRET_KEY=<redacted>",
        "SENTRY_DSN=https://<redacted>@o1.ingest.sentry.io/<redacted>",
        "DB_PASSWORD_URL=postgres://<redacted>@db/<redacted>",
    ]


def test_names_and_entries_without_a_value_stay():
    assert redact_env(["NOEQ", "EMPTY=", "C=x=y", "U=http://db:1=2"]) == [
        "NOEQ",
        "EMPTY=<redacted>",
        "C=<redacted>",
        "U=<redacted>",
    ]


def test_the_value_starts_after_the_first_equals_sign():
    assert redact_env(["A=postgres://u:p=q@db/x"]) == ["A=postgres://<redacted>@db/<redacted>"]


def test_a_host_of_ten_thousand_characters_is_hidden():
    assert shown("https://" + "a" * 10_000 + ".com") == "<redacted>"
    assert shown("https://" + ".".join(["a" * 63] * 160)) == "<redacted>"


def test_no_password_shape_shows_anything_but_the_real_host():
    """Every password made of URL delimiters either leaves `db:5432` as the place or hides the
    whole value: no reading may take part of the password for the host or port."""
    rng = random.Random(378)
    alphabet = "@:/?#[]\\%.;,=&+ \t\nab09_-~ＡＢ"
    for _ in range(3_000):
        password = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))
        for scheme in ("postgres", "https"):
            out = shown(f"{scheme}://user:{password}@db:5432/shop?x=1")
            assert out in ("<redacted>", f"{scheme}://<redacted>@db:5432/<redacted>?<redacted>")


def test_no_path_query_or_fragment_shape_shows_more_than_its_delimiters():
    rng = random.Random(3780)
    alphabet = "@:/?#[]\\%.;,=&+ \t\nab09_-~ＡＢ<>"
    place = re.compile(
        r"<redacted>|https://db:5432(/(<redacted>)?)?(\?(<redacted>)?)?(#(<redacted>)?)?"
    )
    for _ in range(3_000):
        tail = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 16)))
        for start in "/?#":
            assert place.fullmatch(shown(f"https://db:5432{start}{tail}"))
