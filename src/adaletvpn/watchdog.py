import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from adaletvpn.errors import CodedError, is_exact_uuid, worker_environment


TRIAL_SECONDS = 180
RETRY_SECONDS = 120
TRIAL_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "trials"


def write_status(path, owned_uuid, state):

    path = Path(path)
    temporary = path.with_suffix(".tmp")
    with open(temporary, "w", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)) as output:
        json.dump({"uuid": owned_uuid, "state": state, "updated_at": time.time()}, output)
    os.replace(temporary, path)


async def run_guard(transport, owned_uuid, status_path, seconds=TRIAL_SECONDS,
                    retry_seconds=RETRY_SECONDS, interval=2, attempt_timeout=45):
    if not is_exact_uuid(owned_uuid):
        raise ValueError("Geçersiz oturum kimliği")
    if any(not math.isfinite(v) or v <= 0 for v in (seconds, retry_seconds, interval, attempt_timeout)):
        raise ValueError("Geçersiz süre")

    deadline = time.monotonic() + seconds
    write_status(status_path, owned_uuid, "armed")
    await asyncio.sleep(max(0, deadline - time.monotonic()))
    write_status(status_path, owned_uuid, "cleaning")
    retry_deadline = time.monotonic() + retry_seconds
    while time.monotonic() < retry_deadline:
        try:
            await asyncio.wait_for(transport.recover(owned_uuid, require_profile=True),
                                   min(attempt_timeout, retry_deadline - time.monotonic()))
        except Exception:

            await asyncio.sleep(min(interval, max(0, retry_deadline - time.monotonic())))
        else:
            write_status(status_path, owned_uuid, "cleaned")
            return True

    write_status(status_path, owned_uuid, "unconfirmed")
    return False


async def arm_watchdog(owned_uuid):
    from adaletvpn.networkmanager import LIVE_ENABLED
    if not LIVE_ENABLED:
        raise CodedError("watchdog_unavailable")
    if not is_exact_uuid(owned_uuid):
        raise CodedError("watchdog_unavailable")
    root = TRIAL_DIRECTORY
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="trial-", dir=root))
    status_path = directory / "watchdog.json"
    env = worker_environment()

    process = subprocess.Popen(
        [sys.executable, "-m", "adaletvpn.watchdog", "--uuid", owned_uuid, "--status", str(status_path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True, env=env)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CodedError("watchdog_unavailable")
        try:
            status = json.loads(status_path.read_text())
            if status.get("uuid") == owned_uuid and status.get("state") == "armed":
                return status_path
        except (OSError, ValueError):
            pass
        await asyncio.sleep(0.05)
    raise CodedError("watchdog_unavailable")


async def main(owned_uuid, status_path):
    from adaletvpn.networkmanager import GioTransport, run_with_glib
    transport = GioTransport()
    return await run_with_glib(run_guard(transport, owned_uuid, status_path))


if __name__ == "__main__":
    from adaletvpn.errors import seal_live_worker
    seal_live_worker()
    parser = argparse.ArgumentParser()
    parser.add_argument("--uuid", required=True)
    parser.add_argument("--status", required=True)
    arguments = parser.parse_args()
    raise SystemExit(0 if asyncio.run(main(arguments.uuid, arguments.status)) else 1)
