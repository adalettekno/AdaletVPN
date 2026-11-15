"""Allowlisted diagnostics: never record exception messages, locals or secrets."""

import logging
from enum import Enum


class Stage(Enum):
    UI_REFRESH = "ui_refresh"
    UI_TASK = "ui_task"
    EVENT_LOOP = "event_loop"
    VAULT_TASK = "vault_task"


_LOG = logging.getLogger("adaletvpn")


def report_failure(stage: Stage, error: BaseException | None = None) -> None:
    # A traceback can contain user data in source lines and exception messages.
    # Keep only code locations, with no source text or arbitrary object reprs.
    locations = []
    trace = error.__traceback__ if error else None
    while trace is not None:
        from pathlib import Path

        locations.append(f"{Path(trace.tb_frame.f_code.co_filename).name}:{trace.tb_lineno}")
        trace = trace.tb_next
    _LOG.error(
        "operation=%s code=unexpected_failure locations=%s", stage.value, ",".join(locations) or "unavailable"
    )
