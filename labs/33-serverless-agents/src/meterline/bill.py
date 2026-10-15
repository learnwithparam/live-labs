"""The cloud bill for one night, computed from published prices and what the run actually used.

Lambda bills what ran: GB-seconds while a handler runs, one request per invoke, and durable operations
(start, each step attempt, each callback). An always-on VM bills every hour of the day, busy or idle.
"""

from meterline.config import (DURABLE_PER_GB_WRITTEN, DURABLE_PER_OPERATION, FUNCTION_MEMORY_GB, LAMBDA_ARM_PER_GB_SECOND,
                              LAMBDA_PER_REQUEST, VM_NAME, VM_PER_HOUR)

# Event types the durable service bills as an operation: each step attempt, each callback, each start.
BILLED_EVENTS = {"ExecutionStarted", "StepStarted", "CallbackStarted", "WaitStarted", "ContextStarted"}


def lambda_bill(invocation_seconds: list[float], operations: int, bytes_written: int) -> dict:
    """Pay per use. A crashed invoke is still billed up to the moment it died, so it is counted."""
    gb_seconds = sum(invocation_seconds) * FUNCTION_MEMORY_GB
    compute = gb_seconds * LAMBDA_ARM_PER_GB_SECOND
    requests = len(invocation_seconds) * LAMBDA_PER_REQUEST
    durable = operations * DURABLE_PER_OPERATION + bytes_written / 1e9 * DURABLE_PER_GB_WRITTEN
    return {"invokes": len(invocation_seconds), "gb_seconds": round(gb_seconds, 1), "operations": operations,
            "compute": compute, "requests": requests, "durable": durable, "total": compute + requests + durable}


def vm_bill(hours: float = 24.0) -> dict:
    # The VM's price does not depend on the run: it is paid for the whole day so the job has somewhere to run.
    return {"name": VM_NAME, "hours": hours, "total": hours * VM_PER_HOUR}
