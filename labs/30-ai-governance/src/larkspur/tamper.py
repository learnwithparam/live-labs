"""What an insider with the database file could do, run on a copy so the real log stays whole.

The triggers stop an UPDATE or DELETE through the normal connection, so the insider drops them first.
That is the honest threat: anyone with the file can rewrite it. The chain does not prevent the edit; it
makes the edit visible to anyone who verifies.
"""

import json
import shutil
import sqlite3
from pathlib import Path


def tampered_copy(source: Path, target: Path, mode: str, seq: int) -> str:
    """Copy the log, then soften one reply ("edit") or remove one record ("delete"). Returns what was done."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    connection = sqlite3.connect(target)
    try:
        connection.executescript("DROP TRIGGER audit_no_update; DROP TRIGGER audit_no_delete;")
        if mode == "delete":
            connection.execute("DELETE FROM audit_record WHERE seq = ?", (seq,))
            done = f"deleted record {seq}"
        else:
            record = json.loads(connection.execute("SELECT record FROM audit_record WHERE seq = ?", (seq,)).fetchone()[0])
            # Rewrite what the customer was shown, the edit an insider would want after a complaint.
            record["delivered.text"] = "A colleague will call you today to reverse the duplicate payment."
            connection.execute("UPDATE audit_record SET record = ? WHERE seq = ?",
                               (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False), seq))
            done = f"rewrote the reply in record {seq}"
        connection.commit()
    finally:
        connection.close()
    return done
