"""Pure presentation decisions, independent of GTK widgets and secret values."""

from dataclasses import dataclass

from adaletvpn.core import State


@dataclass(frozen=True)
class ConnectionView:
    editable: bool
    in_progress: bool
    show_stop: bool
    can_stop: bool
    can_connect: bool
    status_message: str
    tone: str
    badge: str

    @classmethod
    def from_controller(
        cls,
        controller,
        *,
        busy=False,
        closing=False,
        cancelling=False,
        read_only=False,
        managed=False,
        credentials_valid=False,
    ):
        state = controller.state
        connected = state in (State.CONNECTED, State.UNKNOWN)
        editable = not busy and not closing and controller.can_start
        progress = busy or state in (
            State.SCANNING,
            State.AUTHENTICATING,
            State.CONNECTING,
            State.DISCONNECTING,
        )
        selected = next((c for c in controller.certificates if c.identifier == controller.selected_id), None)
        allowed = bool(selected) and (not managed or selected.problem_code() is None)
        status = (
            f"{len(controller.certificates)} sertifika listelendi"
            if read_only and not busy and state == State.DISCONNECTED
            else state.value
        )
        tone = {State.CONNECTED: "online", State.DISCONNECTED: "offline", State.ERROR: "error"}.get(
            state, "working"
        )
        badge = (
            "working"
            if progress
            else "online"
            if state == State.CONNECTED
            else "offline"
            if state == State.DISCONNECTED
            else "working"
        )
        return cls(
            editable,
            progress,
            busy or connected or not controller.can_start,
            not closing and not cancelling and (busy or connected or state == State.ERROR),
            not read_only and editable and allowed and credentials_valid,
            status,
            tone,
            badge,
        )
