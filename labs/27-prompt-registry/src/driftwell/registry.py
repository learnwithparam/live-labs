"""The prompt registry: every version of the support bot's prompt lives in Langfuse, never in the code.

A version is immutable once created. What changes is the label: `production` and `staging` each sit on
exactly one version, and moving a label is the whole release, and the whole rollback.
"""

from dataclasses import dataclass, field

from driftwell.config import PROMPT_NAME


@dataclass
class Version:
    number: int
    text: str
    labels: list[str] = field(default_factory=list)
    message: str = ""
    author: str = ""


def versions(client, name: str = PROMPT_NAME) -> list[Version]:
    """Every version of the prompt, oldest first, with the labels it holds right now."""
    listing = client.api.prompts.list(name=name)
    found = next((meta for meta in listing.data if meta.name == name), None)
    if found is None:
        return []
    out = []
    for number in sorted(found.versions):
        fetched = client.api.prompts.get(name, version=number)
        # `latest` is Langfuse's own pointer to the newest version; it is not a release decision.
        labels = [label for label in fetched.labels if label != "latest"]
        out.append(Version(number, fetched.prompt, labels, fetched.commit_message or "",
                           (fetched.config or {}).get("author", "")))
    return out


def holder(all_versions: list[Version], label: str) -> Version | None:
    """The one version a label points at, or None when nobody holds it."""
    return next((version for version in all_versions if label in version.labels), None)


def propose(client, text: str, message: str, author: str, name: str = PROMPT_NAME) -> int:
    """Save an edit as a new version on `staging`. It reaches no customer until a person promotes it."""
    if not text.strip():
        # An empty prompt would make the bot answer from nothing; refuse it at the door.
        raise ValueError("refusing to save an empty prompt")
    created = client.create_prompt(name=name, prompt=text, labels=["staging"], commit_message=message,
                                   config={"author": author})
    return created.version


def import_from_code(client, text: str, name: str = PROMPT_NAME) -> int:
    """The prompt that used to live in the code becomes version 1, already in production."""
    created = client.create_prompt(name=name, prompt=text, labels=["production"],
                                   commit_message="Imported from src/driftwell/naive.py",
                                   config={"author": "engineering"})
    return created.version


def move_label(client, version: int, label: str, name: str = PROMPT_NAME) -> None:
    """Point `label` at `version`. Langfuse takes it off whichever version held it before.

    This calls the API directly. The SDK's `update_prompt` also empties this process's prompt cache, which
    would hide the wait that a bot in another process really has.
    """
    client.api.prompt_version.update(name=name, version=version, new_labels=[label])


def delete_all(client, name: str = PROMPT_NAME) -> None:
    """Start the walkthrough from a clean registry, so version numbers match the README."""
    try:
        client.api.prompts.delete(name)
    except Exception as error:
        # A registry that never had the prompt is already clean.
        if type(error).__name__ != "NotFoundError":
            raise
