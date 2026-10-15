"""Turn each source system's own access list into OpenFGA relationship tuples.

The drive, the wiki and the ticket tool each say who may open what, in their own words: Drive has
permissions on files and folders, Confluence has space permissions and page restrictions, Jira has
project browse rights and security levels. Search must obey all three, so the sync translates each into
one shared vocabulary of tuples (user, relation, object) and the authorization model does the rest.

A source list that names someone the directory does not know is not guessed at. The document is held,
visible to its owner only, until an admin decides: the fail-closed rule a leak-free search needs.
"""

import json
from pathlib import Path

from copperline.config import DATA, OUT

DECISIONS = OUT / "decisions.json"
# Drive roles collapse to the two relations search cares about; commenters may read, so they view.
DRIVE_ROLES = {"owner": "owner", "writer": "editor", "commenter": "viewer", "reader": "viewer"}


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


def directory() -> dict:
    return load("directory.json")


def decisions(path: Path = DECISIONS) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


class Translator:
    """Resolves the names a source uses (emails, group keys) to directory subjects, or records why not."""

    def __init__(self, people: dict, admin_decisions: dict):
        self.by_email = {user["email"]: f"user:{user['id']}" for user in people["users"]}
        self.by_email |= {group["email"]: f"group:{group['id']}#member" for group in people["groups"]}
        self.user_ids = {user["id"] for user in people["users"]}
        self.group_ids = {group["id"] for group in people["groups"]}
        # A group with no member anywhere is a typo or an outside group: an admin maps it once, and every
        # later sync reuses the answer, so the same question is never asked twice.
        self.mapped = {unknown: known for decision in admin_decisions.values()
                       for unknown, known in decision.get("map", {}).items()}
        self.problems: list[str] = []

    def email(self, address: str) -> str | None:
        subject = self.by_email.get(address)
        if subject is None:
            self.problems.append(f"names {address}, who is not in the company directory")
        return subject

    def group(self, key: str) -> str | None:
        key = self.mapped.get(key, key)
        if key in self.group_ids:
            return f"group:{key}#member"
        self.problems.append(f"names the group {key!r}, which the company directory does not have")
        return None

    def user(self, key: str) -> str | None:
        if key in self.user_ids:
            return f"user:{key}"
        self.problems.append(f"names the user {key!r}, who is not in the company directory")
        return None


def drive_tuples(drive: dict, names: Translator) -> list[tuple[str, str, str]]:
    tuples = []
    for item in drive["folders"] + drive["files"]:
        kind = "folder" if item["id"].startswith("fld-") else "document"
        target = f"{kind}:{item['id']}"
        # Drive inherits down the folder tree, which the model's `viewer from parent` already says.
        if item["parent"]:
            tuples.append((f"folder:{item['parent']}", "parent", target))
        tuples.append((names.email(item["owner"]), "owner", target))
        for grant in item["permissions"]:
            relation = DRIVE_ROLES[grant["role"]]
            if grant["type"] in ("user", "group"):
                tuples.append((names.email(grant["emailAddress"]), relation, target))
            elif grant["type"] == "domain":
                # Everyone at copperline.io, which is all staff; a contractor's own domain is not in it.
                tuples.append(("group:all-staff#member", relation, target))
            elif grant["type"] == "anyone":
                # "Anyone with the link" is not "everyone": only people who opened the link hold it.
                link = f"link:{item['id']}"
                tuples.append((link, "shared_link", target))
                tuples += [(names.email(address), "recipient", link) for address in item.get("openedByLinkBy", [])]
    return tuples


def wiki_tuples(wiki: dict, names: Translator) -> list[tuple[str, str, str]]:
    tuples = []
    for space in wiki["spaces"]:
        view = space["permissions"]["view"]
        target = f"folder:space-{space['key']}"
        tuples += [(names.group(key), "viewer", target) for key in view["groups"]]
        tuples += [(names.user(key), "viewer", target) for key in view["users"]]
    for page in wiki["pages"]:
        target = f"document:{page['id']}"
        restricted = page["restrictions"] and page["restrictions"]["read"]
        if not restricted:
            tuples.append((f"folder:space-{page['space']}", "parent", target))
            continue
        # A restriction narrows: the page stops inheriting its space and lists its readers itself.
        tuples += [(names.group(key), "viewer", target) for key in restricted["groups"]]
        tuples += [(names.user(key), "viewer", target) for key in restricted["users"]]
    return tuples


def ticket_tuples(tickets: dict, names: Translator) -> list[tuple[str, str, str]]:
    tuples = []
    levels = {level["name"]: level for level in tickets["security_levels"]}
    for project in tickets["projects"]:
        target = f"folder:project-{project['key']}"
        tuples += [(names.group(key), "viewer", target) for key in project["browse"]["groups"]]
        tuples += [(names.user(key), "viewer", target) for key in project["browse"]["users"]]
    for issue in tickets["issues"]:
        target = f"document:{issue['key']}"
        level = levels.get(issue["security_level"])
        if level is None:
            tuples.append((f"folder:project-{issue['project']}", "parent", target))
            continue
        # A security level replaces the project's audience; Copperline's levels always include the reporter.
        tuples.append((names.user(issue["reporter"]), "viewer", target))
        tuples += [(names.group(key), "viewer", target) for key in level["groups"]]
        tuples += [(names.user(key), "viewer", target) for key in level["users"]]
    return tuples


def documents() -> list[dict]:
    """Every searchable document in one shape: id, title, which system, where it lives, its text."""
    drive, wiki, tickets = load("sources/drive.json"), load("sources/wiki.json"), load("sources/tickets.json")
    folders = {folder["id"]: folder["name"] for folder in drive["folders"]}
    spaces = {space["key"]: space["name"] for space in wiki["spaces"]}
    return ([{"id": item["id"], "title": item["title"], "source": "Drive", "place": folders[item["parent"]],
              "text": item["body"]} for item in drive["files"]]
            + [{"id": page["id"], "title": page["title"], "source": "Wiki", "place": spaces[page["space"]],
                "text": page["body"]} for page in wiki["pages"]]
            + [{"id": issue["key"], "title": f"{issue['key']} {issue['summary']}", "source": "Tickets",
                "place": issue["project"], "text": issue["description"]} for issue in tickets["issues"]])


def owner_of(document_id: str) -> str:
    """Who a held document stays visible to: its Drive owner, wiki author or ticket reporter."""
    for item in load("sources/drive.json")["files"]:
        if item["id"] == document_id:
            return item["owner"].split("@")[0]
    for page in load("sources/wiki.json")["pages"]:
        if page["id"] == document_id:
            return page["author"]
    return next(issue["reporter"] for issue in load("sources/tickets.json")["issues"] if issue["key"] == document_id)


def build(admin_decisions: dict | None = None) -> dict:
    """All tuples for the directory and the three sources, and the documents held for an admin."""
    people = directory()
    admin_decisions = decisions() if admin_decisions is None else admin_decisions
    tuples = []
    for group in people["groups"]:
        for member in group["members"]:
            subject = f"{member}#member" if member.startswith("group:") else f"user:{member}"
            tuples.append((subject, "member", f"group:{group['id']}"))
    held = []
    sources = [("drive", drive_tuples), ("wiki", wiki_tuples), ("tickets", ticket_tuples)]
    for source, translate in sources:
        exported = load(f"sources/{source}.json")
        # Translate one document at a time, so one bad name holds that document and nothing else.
        for single in split(source, exported):
            names = Translator(people, admin_decisions)
            translated = translate(single, names)
            document_ids = [target.removeprefix("document:") for _, _, target in translated if target.startswith("document:")]
            document_id = document_ids[0] if document_ids else None
            if names.problems and not document_id:
                # A folder, space or project with a bad name would hide or expose everything inside it.
                raise SystemExit(f"{source}: a container's access list {names.problems[0]}. Fix the export first.")
            if names.problems:
                owner = owner_of(document_id)
                held.append({"document": document_id, "source": source, "reasons": names.problems, "owner": owner,
                             "decision": admin_decisions.get(document_id, {}).get("keep") and "kept private"})
                tuples.append((f"user:{owner}", "viewer", f"document:{document_id}"))
            else:
                tuples += translated
    return {"tuples": sorted(set(tuples)), "held": held}


def split(source: str, exported: dict) -> list[dict]:
    """One export per document, each carrying the containers it needs (folders, spaces, projects)."""
    if source == "drive":
        return [{"folders": exported["folders"], "files": []}] + [
            {"folders": [], "files": [item]} for item in exported["files"]]
    if source == "wiki":
        return [{"spaces": exported["spaces"], "pages": []}] + [
            {"spaces": [], "pages": [page]} for page in exported["pages"]]
    return [{**exported, "issues": []}] + [
        {"projects": [], "security_levels": exported["security_levels"], "issues": [issue]} for issue in exported["issues"]]


def people() -> dict[str, dict]:
    """Each person with the groups they are directly in: what the naive prompt tells the model."""
    found = directory()
    names = {group["id"]: group["name"] for group in found["groups"]}
    return {user["id"]: {**user, "groups": [names[group["id"]] for group in found["groups"] if user["id"] in group["members"]]}
            for user in found["users"]}


def labels() -> dict[str, str]:
    """A readable name for every object a relation path can pass through."""
    found = {f"user:{user['id']}": user["name"] for user in directory()["users"]}
    found |= {f"group:{group['id']}": group["name"] for group in directory()["groups"]}
    drive, wiki, tickets = load("sources/drive.json"), load("sources/wiki.json"), load("sources/tickets.json")
    found |= {f"folder:{folder['id']}": f"the {folder['name']} folder" for folder in drive["folders"]}
    found |= {f"folder:space-{space['key']}": f"the {space['name']} wiki space" for space in wiki["spaces"]}
    found |= {f"folder:project-{project['key']}": f"the {project['key']} project" for project in tickets["projects"]}
    found |= {f"document:{document['id']}": document["title"] for document in documents()}
    found |= {f"link:{item['id']}": f"the shared link to {item['title']}" for item in drive["files"]}
    return found


def record_decision(document_id: str, decision: dict, path: Path = DECISIONS) -> dict:
    """Keep an admin's answer for a held document; the next sync applies it to every later run."""
    saved = decisions(path)
    saved[document_id] = decision
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(saved, indent=1))
    return saved
