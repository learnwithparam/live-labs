"""A scripted vision model, so reading, pricing and routing are tested with no key and no network.

It answers each photo with the adjuster's confirmed findings, which makes the expected estimates the
ones in the adjuster's file, and says the photos disagree whenever the Dodge photo is in the request.
"""

import asyncio
import json
from types import SimpleNamespace

from saltmarsh.claims import photo_ids, truth
from saltmarsh.reader import image_part

FOREIGN_CAR = "P12"


def reply(content: str, tokens: tuple[int, int] = (700, 120)):
    """One chat completion in the shape the openai client returns."""
    usage = SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1], prompt_tokens_details=None,
                            model_extra={"cost": 0.0001})
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


class FakeModel:
    """Answers by the request's schema name, and records how many calls were in flight at once."""

    def __init__(self, free_text: str = "Roughly £900 to £1,100 including paint."):
        self.free_text = free_text
        self.by_url = {image_part(photo_id)["image_url"]["url"]: photo_id for photo_id in photo_ids()}
        self.labels = truth()["photos"]
        self.in_flight, self.most_in_flight, self.requests = 0, 0, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def photos_in(self, request: dict) -> list[str]:
        parts = request["messages"][-1]["content"]
        return [self.by_url[part["image_url"]["url"]] for part in parts if part["type"] == "image_url"]

    async def create(self, **request):
        self.requests.append(request)
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        # A pause, so overlapping calls show up as overlap if the code allows it.
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        photos = self.photos_in(request)
        schema = request.get("response_format", {}).get("json_schema", {}).get("name")
        if schema == "same_vehicle":
            differs = FOREIGN_CAR in photos
            return reply(json.dumps({"same_vehicle": not differs,
                                     "reason": "The second photo shows a Dodge." if differs else "Same badge and plate."}))
        if schema == "photo_reading":
            label = self.labels[photos[0]]
            findings = [{**finding, "damage": "rust" if finding["prior_damage"] else "dent", "box": [100, 200, 600, 700]}
                        for finding in label["findings"]]
            return reply(json.dumps({"vehicle": {"make": "Suzuki", "colour": "blue", "view": "front"},
                                     "usable": label["usable"], "findings": findings,
                                     "quality_problem": "" if label["usable"] else "Glare hides the bonnet."}))
        return reply(self.free_text)
