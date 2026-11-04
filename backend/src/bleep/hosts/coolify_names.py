"""How a Coolify application's name is written into its `coolify.resourceName` label, and how a
name is matched against those labels. Shared by the plan check and the Coolify plugin."""

import re
import unicodedata
from collections.abc import Collection


def coolify_slug(name: str) -> str:
    """`name` as Coolify writes it into `coolify.resourceName`: Laravel's `Str::slug`
    (`defaultLabels` in Coolify's bootstrap/helpers/docker.php). Lower-case ASCII, `_` and runs
    of spaces or `-` become one `-`, `@` reads `at`, anything else that is not a letter or a
    digit is dropped, and `-` is trimmed: `Shop API` is `shop-api`, `static:main` `staticmain`.
    Laravel transliterates with a table; NFKD with the marks dropped stands in for it, so a
    name with `ß`, `Ł` or a non-Latin script slugs differently (docs/design/guard.md, Limits)."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"_+", "-", text).replace("@", "-at-")
    text = re.sub(r"[^-a-z0-9\s]+", "", text.lower())
    return re.sub(r"[-\s]+", "-", text).strip("-")


def name_matches(name: str, labels: Collection[str]) -> bool:
    """Whether `name` is one of the `coolify.resourceName` `labels`: as Coolify's slug of it, or
    exactly as written (a label from an older Coolify is not a slug), in any case. A name that
    slugs to nothing matches by its exact spelling only."""
    folded = {label.casefold() for label in labels if label}
    slug = coolify_slug(name)
    return name.casefold() in folded or (bool(slug) and slug in folded)
