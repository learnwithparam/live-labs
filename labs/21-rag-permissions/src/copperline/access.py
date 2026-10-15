"""Ask OpenFGA who may open what: check, batch check, list objects and expand.

Search never decides access itself. It writes the sources' tuples into OpenFGA once, then asks OpenFGA
for every document a chunk came from. The authorization model in `data/model.fga` is the only place
that says how groups nest, how folders pass access down and what a shared link grants.
"""

import json
from pathlib import Path

from openfga_sdk.client import ClientConfiguration
from openfga_sdk.client.models import (ClientBatchCheckItem, ClientBatchCheckRequest, ClientExpandRequest,
                                       ClientListObjectsRequest, ClientTuple, ClientWriteRequest)
from openfga_sdk.models.create_store_request import CreateStoreRequest
from openfga_sdk.models.write_authorization_model_request import WriteAuthorizationModelRequest
from openfga_sdk.sync import OpenFgaClient

from copperline.config import DATA, OUT, fga_url

STORE_FILE = OUT / "fga.json"
WRITE_LIMIT = 100  # OpenFGA takes at most 100 tuples in one write


def client(store_id: str | None = None, model_id: str | None = None) -> OpenFgaClient:
    return OpenFgaClient(ClientConfiguration(api_url=fga_url(), store_id=store_id, authorization_model_id=model_id))


def load_store(tuples: list[tuple[str, str, str]], store_file: Path = STORE_FILE) -> dict:
    """A fresh store with the model and every tuple. The server keeps it in memory, so each sync starts clean."""
    with client() as fga:
        store = fga.create_store(CreateStoreRequest(name="copperline-search"))
        fga.set_store_id(store.id)
        model = json.loads((DATA / "model.json").read_text())
        model_id = fga.write_authorization_model(WriteAuthorizationModelRequest(**model)).authorization_model_id
        fga.set_authorization_model_id(model_id)
        for start in range(0, len(tuples), WRITE_LIMIT):
            fga.write(ClientWriteRequest(writes=[ClientTuple(user=subject, relation=relation, object=target)
                                                 for subject, relation, target in tuples[start:start + WRITE_LIMIT]]))
    ids = {"store_id": store.id, "model_id": model_id, "url": fga_url()}
    store_file.parent.mkdir(exist_ok=True)
    store_file.write_text(json.dumps(ids, indent=1))
    return ids


class Authorizer:
    """The three questions search asks OpenFGA, for one store and one model version."""

    def __init__(self, store_file: Path = STORE_FILE):
        if not store_file.exists():
            raise SystemExit("No OpenFGA store yet. Start OpenFGA, then run `uv run search sync`.")
        ids = json.loads(store_file.read_text())
        self.fga = client(ids["store_id"], ids["model_id"])

    def allowed(self, user: str, document_ids: list[str]) -> set[str]:
        """Post-filter: one batch check for every document the retriever found, before the model sees any."""
        unique = sorted(set(document_ids))
        if not unique:
            return set()
        checks = [ClientBatchCheckItem(user=f"user:{user}", relation="viewer", object=f"document:{document_id}",
                                       correlation_id=str(position)) for position, document_id in enumerate(unique)]
        response = self.fga.batch_check(ClientBatchCheckRequest(checks=checks))
        # An errored check is a no. Failing open on an error is how a timeout becomes a leak.
        return {unique[int(single.correlation_id)] for single in response.result if single.allowed and not single.error}

    def viewable(self, user: str) -> set[str]:
        """Pre-filter: every document this user may open, so retrieval only ever searches those."""
        response = self.fga.list_objects(ClientListObjectsRequest(user=f"user:{user}", relation="viewer", type="document"))
        return {target.removeprefix("document:") for target in response.objects}

    def write(self, tuples: list[tuple[str, str, str]]) -> None:
        self.fga.write(ClientWriteRequest(writes=[ClientTuple(user=subject, relation=relation, object=target)
                                                  for subject, relation, target in tuples]))

    def why(self, user: str, document_id: str) -> list[dict]:
        """The relation path that lets `user` view the document, read from OpenFGA's expand tree.

        Check answers only yes or no. Expand returns the userset tree behind a relation, so walking it
        finds the chain a person needs to trust the answer: user, group, folder, document.
        """
        return self.walk(f"user:{user}", f"document:{document_id}", "viewer", set()) or []

    def walk(self, user: str, target: str, relation: str, seen: set) -> list[dict] | None:
        if (target, relation) in seen:
            return None
        seen.add((target, relation))
        tree = self.fga.expand(ClientExpandRequest(relation=relation, object=target)).tree.root
        for node in (tree.union.nodes if tree.union else [tree]):
            leaf = node.leaf
            if leaf is None:
                continue
            for subject in (leaf.users.users if leaf.users else []):
                if subject == user:
                    return [{"subject": user, "relation": relation, "object": target}]
                if "#" in subject:
                    group, group_relation = subject.split("#")
                    path = self.walk(user, group, group_relation, seen)
                    if path:
                        return path + [{"subject": subject, "relation": relation, "object": target}]
            if leaf.computed:
                computed_obj, computed_relation = leaf.computed.userset.split("#")
                path = self.walk(user, computed_obj, computed_relation, seen)
                if path:
                    return path + [{"subject": f"{computed_obj}#{computed_relation}", "relation": relation, "object": target}]
            if leaf.tuple_to_userset:
                # `viewer from parent`: each parent's viewers are this object's viewers.
                for computed in leaf.tuple_to_userset.computed:
                    parent_obj, parent_relation = computed.userset.split("#")
                    path = self.walk(user, parent_obj, parent_relation, seen)
                    if path:
                        via = leaf.tuple_to_userset.tupleset.split("#")[1]
                        return path + [{"subject": parent_obj, "relation": via, "object": target, "grants": relation}]
        return None


ROLES = {"owner": "an owner", "editor": "an editor"}
VERBS = {"viewer": "can view", "editor": "can edit", "owner": "owns", "recipient": "opened"}


def describe(path: list[dict], labels: dict[str, str]) -> list[str]:
    """The relation path as sentences a person reads under "Why can I see this?"."""
    lines = []
    for step in path:
        subject, relation, target = step["subject"], step["relation"], step["object"]
        subject_name = labels.get(subject.split("#")[0], subject)
        obj_name = labels.get(target, target)
        if "grants" in step and relation == "parent":
            lines.append(f"{obj_name} sits in {subject_name}, so it inherits who can view it")
        elif "grants" in step:
            lines.append(f"anyone holding {subject_name} can view it")
        elif subject.endswith("#member") and relation == "member":
            lines.append(f"everyone in {subject_name} is in {obj_name}")
        elif subject.endswith("#member"):
            lines.append(f"{subject_name} {VERBS[relation]} {obj_name}")
        elif "#" in subject:
            # A computed step on the same object, such as "owners are editors, editors are viewers".
            lines.append(f"{ROLES[subject.split('#')[1]]} can also {VERBS[relation].removeprefix('can ')} it")
        elif relation == "member":
            lines.append(f"{subject_name} is in {obj_name}")
        else:
            lines.append(f"{subject_name} {VERBS[relation]} {obj_name}")
    return lines
