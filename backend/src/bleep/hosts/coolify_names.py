"""How a Coolify application's name is written into its `coolify.resourceName` label, how a
name is matched against those labels, and which protected entry names the application a
container belongs to. Shared by the plan check, discovery and the Coolify plugin."""

import re
import unicodedata
from collections.abc import Collection, Mapping

from bleep.schemas.docker import ContainerSummary

# The labels a coolify.* target is matched on. Not `coolify.applicationId`: Coolify's API never
# shows an application's id, and a bare number could match another application's name.
COOLIFY_UUID, COOLIFY_NAME = "coolify.applicationUuid", "coolify.resourceName"
COOLIFY_APP_LABELS = (COOLIFY_UUID, COOLIFY_NAME)
# Coolify 4.1.x wrote no application uuid label; its uuid is in these and in the container name.
COOLIFY_MANAGED, COOLIFY_TYPE, COOLIFY_SHORT_NAME = (
    "coolify.managed",
    "coolify.type",
    "coolify.name",
)


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


def coolify_container_name(name: str, uuid: str) -> bool:
    """Whether `name` is a name Coolify gives a container of the application `uuid`:
    `<uuid>-<digits>` or `<service>-<uuid>-<digits>`, anchored at both ends."""
    return bool(uuid) and re.search(rf"(?:^|-){re.escape(uuid)}-\d+$", name) is not None


def app_protected_by(container: ContainerSummary, protected: Collection[str]) -> str | None:
    """The entry of `protected` that names the Coolify application `container` belongs to, by
    the uuid or resource name label the engine reports on it, or None.

    Coolify names an app's containers anew at every deploy, so its uuid and its name protect it
    too, not only a container name that lasts until the next deploy. A uuid compares exactly. The
    name label is Coolify's slug of the app's name, so a protected name is compared slugged, and
    also exactly and in any case, for a label an older Coolify wrote that is not a slug
    (`name_matches`). A Coolify 4.1.x container carries no uuid label, so on a container with
    Coolify labels an entry also counts when it is the `coolify.name` label or the uuid in
    Coolify's container name (`coolify_container_name`). The labels count without the Coolify
    plugin: Bleep can watch a Coolify host with the plugin off."""
    labels = container.labels
    uuid = labels.get(COOLIFY_UUID)
    if uuid and uuid in protected:
        return uuid
    name = labels.get(COOLIFY_NAME)
    by_name = next((p for p in sorted(protected) if name and name_matches(p, {name})), None)
    if by_name is not None or not _coolify_labelled(labels):
        return by_name
    return next(
        (
            p
            for p in sorted(protected)
            if p == labels.get(COOLIFY_SHORT_NAME) or coolify_container_name(container.name, p)
        ),
        None,
    )


def _coolify_labelled(labels: Mapping[str, str]) -> bool:
    """Whether `labels` mark a Coolify container; `coolify.applicationId` alone counts, as it
    does for discovery."""
    return (
        labels.get(COOLIFY_MANAGED) == "true"
        or COOLIFY_TYPE in labels
        or bool(labels.get("coolify.applicationId"))
    )
