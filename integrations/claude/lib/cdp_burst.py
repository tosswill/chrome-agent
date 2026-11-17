"""Send a timed sequence of CDP commands to one tab over a single connection.

Run with chrome-agent's own Python (it has the CDP client), reading a JSON spec on stdin:

    {"port": 9228, "target": "<full target id>",
     "steps": [{"method": "Input.dispatchKeyEvent", "params": {...}, "sleep": 0.08}, ...]}

The caller (research_core.Site.burst) decides the timing; this only replays it. One `chrome-agent`
one-shot per keystroke costs ~0.15 s of process start-up each, which made typing several times slower
than the intended human rhythm. Prints {"sent": N} or {"error": ..., "sent": N}.
"""
import asyncio
import json
import sys

from chrome_agent.cdp_client import CDPClient, get_ws_url


async def run(spec: dict) -> int:
    cdp = CDPClient(ws_url=get_ws_url(port=spec["port"], target_type="browser"))
    await cdp.connect()
    sent = 0
    try:
        sid = (await cdp.send(method="Target.attachToTarget",
                              params={"targetId": spec["target"], "flatten": True}))["sessionId"]
        try:
            for step in spec["steps"]:
                await cdp.send(method=step["method"], params=step.get("params") or {}, session_id=sid)
                sent += 1
                if step.get("sleep"):
                    await asyncio.sleep(step["sleep"])
        finally:
            try:
                await cdp.send(method="Target.detachFromTarget", params={"sessionId": sid})
            except Exception:
                pass
    finally:
        await cdp.close()
    return sent


def main() -> int:
    spec = json.load(sys.stdin)
    try:
        print(json.dumps({"sent": asyncio.run(run(spec))}))
        return 0
    except Exception as e:  # report, don't traceback: the caller turns this into a Fail
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
