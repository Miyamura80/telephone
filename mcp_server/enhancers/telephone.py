"""Run the blocking telephone tools off the event loop with progress heartbeats.

FastMCP calls a headless (sync) tool directly on the event loop, so a
``listen`` that waits minutes for the other agent would stall every other
request. These enhancers move the wait to a worker thread and send progress
notifications while it lasts. Non-blocking tools (``get_messages``,
``hang_up``) run inline, but still on the async path so the per-call quota
check happens off the event loop.
"""

from pydantic import BaseModel

from common import global_config
from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from models.telephone import CallView

_HEARTBEAT_MESSAGE = "waiting for the other agent"


async def _wait_on_line(tool: EnhancedTool) -> CallView:
    return await tool.call_with_heartbeat(
        global_config.telephone.heartbeat_s, _HEARTBEAT_MESSAGE
    )


async def _inline(tool: EnhancedTool) -> BaseModel:
    return tool.call()


# "error": these services mutate the call; never silently re-run them.
for _name in ("dial", "answer", "say", "listen"):
    enhance(_name, fallback="error")(_wait_on_line)
# These never block, so they skip the worker pool and are never stuck behind
# long waits.
enhance("hang_up", fallback="error")(_inline)
enhance("get_messages", fallback="error")(_inline)
