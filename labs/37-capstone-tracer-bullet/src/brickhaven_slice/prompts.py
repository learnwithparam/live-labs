"""The three prompts. The fakes in the tests tell the calls apart by the first words of each."""

EXTRACT_SYSTEM = (
    "Extraction. Read one tenant maintenance message for a rental flat manager and reply with one JSON "
    "object and nothing else. Keys: trade (plumbing, electrical, heating, locksmith, appliance, general or "
    "pest), urgency (low, normal, high or emergency), unit (the flat code such as 4B, or null if the "
    "message names none), safety_flag (true for gas, flooding near electrics, sparks or fire risk, else "
    "false), access_window (when the tenant is home, as short text, or null). Never guess a flat."
)
DRAFT_SYSTEM = (
    "Drafting. Write the dispatch message a coordinator will send to the vendor: two or three plain "
    "sentences naming the flat, the problem, the urgency and the hours the tenant is home. No greeting "
    "line, no markdown."
)
REPAIR_SYSTEM = (
    "Repair. The text below was meant to be one JSON object with the keys trade, urgency, unit, "
    "safety_flag and access_window, but it is not valid. Reply with the corrected JSON object and nothing "
    "else. Do not add facts the text does not contain."
)
