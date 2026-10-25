import asyncio
import json
import sys

from adaletvpn.errors import seal_live_worker
from adaletvpn.networkmanager import GioTransport, run_with_glib


async def serve(transport, read_line, send):
    active = None
    try:
        while line := await read_line():
            sequence = None
            try:
                request = json.loads(line)
                sequence = request["id"]
                if type(sequence) is not int or sequence <= 0:
                    raise ValueError("Geçersiz istek kimliği")
                method, value = request["method"], request["value"]
                if method == "activate" and active is None:
                    active = await transport.activate(value)
                    result = active
                elif method == "state" and active is not None and value == active:
                    result = await transport.state(active)
                elif method == "deactivate" and active is not None and value == active:
                    await transport.deactivate(active)
                    active = None
                    result = None
                elif method == "recover" and active is None:
                    await transport.recover(value)
                    result = None
                elif method == "recover_pending" and active is None:
                    await transport.recover(value, require_profile=True)
                    result = None
                else:
                    raise ValueError("Geçersiz işlem")
                send({"id": sequence, "ok": True, "value": result})
            except (ValueError, KeyError, TypeError):
                send({"id": sequence, "ok": False})
            except BrokenPipeError:
                break
            except Exception:
                send({"id": sequence, "ok": False})
    finally:
        if active is not None:
            await transport.deactivate(active)
        elif hasattr(transport, "delete_profile"):
            await transport.delete_profile()


async def main():
    transport = GioTransport()

    def send(value):
        print(json.dumps(value), flush=True)

    await run_with_glib(serve(transport, lambda: asyncio.to_thread(sys.stdin.readline), send))


if __name__ == "__main__":
    seal_live_worker()
    asyncio.run(main())
