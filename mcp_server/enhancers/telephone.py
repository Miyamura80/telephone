"""Run the blocking telephone tools on a dedicated pool with progress heartbeats.

The SDK runs a headless (sync) tool on anyio's shared worker threads, which are
capped (40 by default) and also serve quota checks and every other sync tool.
A ``listen`` that waits minutes for the other agent would hold one of those
threads the whole time, so a handful of open calls could starve the server.
These enhancers move the wait to the dedicated ``_BLOCKING_POOL`` and send
progress notifications while it lasts. ``get_messages`` and ``hang_up`` never
block, so they stay headless.
"""

from common import global_config
from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from models.telephone import CallView

_HEARTBEAT_MESSAGE = "waiting for the other agent"


async def _wait_on_line(tool: EnhancedTool) -> CallView:
    return await tool.call_with_heartbeat(
        global_config.telephone.heartbeat_s, _HEARTBEAT_MESSAGE
    )


# "error": these services mutate the call; never silently re-run them.
for _name in ("dial", "answer", "say", "listen"):
    enhance(_name, fallback="error")(_wait_on_line)
