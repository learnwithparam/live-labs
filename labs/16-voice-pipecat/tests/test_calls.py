import wave

from lucca.caller import AUDIO_DIR, CALLER_RATE, load_calls
from lucca.till import Menu, Till


def test_every_scripted_line_has_a_committed_recording_the_pipeline_can_read():
    for call in load_calls():
        for line_number, script_line in enumerate(call["lines"], 1):
            with wave.open(str(AUDIO_DIR / f"{call['id']}-{line_number}.wav"), "rb") as recording:
                seconds = recording.getnframes() / recording.getframerate()
                assert (recording.getframerate(), recording.getnchannels(), recording.getsampwidth()) == (CALLER_RATE, 1, 2)
                assert 0.5 < seconds < 8, (call["id"], line_number, script_line["say"])


def test_every_keyed_order_is_one_the_till_can_hold():
    """A keyed order the menu cannot price would make the metric unwinnable, so each line is keyed
    through the till itself."""
    menu = Menu()
    for call in load_calls():
        keyed = call["keyed"]
        if "staff" in keyed:
            assert keyed["staff"]
            continue
        till = Till(menu=menu)
        for line in keyed["lines"]:
            added = till.add_item(menu.items[line["item"]]["name"], line["size"], line["options"], line["quantity"])
            assert sorted(added.options) == sorted(line["options"]), call["id"]
