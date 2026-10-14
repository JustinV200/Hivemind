"""Define Withdrawal: move a device's Warden back to the Hive Stand (Level 1 to Level 0).

The way down is the way up reversed, and shorter, because a Warden that is allowed to go holds no
work. The operator's request is honoured only while the device's Warden is idle (`WATCH`, no task
on its Cell): a busy Warden keeps its Handoffs on the device, where the Queen cannot read them, so
she refuses with a reason instead (`warden_busy`, decided by `.request`). The order is: the packet
stops the Warden on the device (WITHDRAW; if it cannot, nothing has changed and the request is
refused as failed), the rung moves down and `swarm.demoted` is written with the change, the
Warden's entry and connection are dropped, the packet is told its new rung, and the Level 0 Warden
is rebuilt on the Hive Stand under the same id, so the device is again served from here. If that
last step fails the device is a Level 0 one with no Warden yet, which the gate's next pass gives it.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Started by `.flow.Colonizer` as a task of the gate's
    group. Calls into `hivemind.queen.swarm_gate` (its own modules) and `hivemind.swarm` only.

Key invariants:
    - The registry's rung moves only after the packet confirmed the Warden stopped, so a device is
      never recorded as Level 0 with a Warden still running on it.
    - The packet's WITHDRAW is bounded by its deadline; nothing waits on a device indefinitely.
    - A failure before the rung moved changes nothing: the request is refused and cleared.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for withdrawal.
    - hivemind.queen.swarm_gate.colonize.promote for the way up.
"""

from __future__ import annotations

from hivemind.queen.swarm_gate.colonize.env import FlowEnv
from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError
from hivemind.queen.swarm_gate.colonize.events import level_event
from hivemind.queen.swarm_gate.colonize.steps import (
    WITHDRAW_DEADLINE_S,
    StepOrder,
    dismiss_remote,
    send_order,
    tell_device,
)
from hivemind.swarm import DeviceLink, RuntimeLevel, SwarmNode
from waggle.messages.swarm import ColonizeAction, ColonizeOutcome

__all__ = ["Withdrawal"]


class Withdrawal:
    """Bring one idle device Warden back to the Hive Stand."""

    def __init__(self, env: FlowEnv, node: SwarmNode, link: DeviceLink, why: str) -> None:
        """Build the flow; `run` carries it out.

        Args:
            env: The Queen, the Level 0 Wardens, the registry and the listener.
            node: The device, on the second rung, as the registry holds it.
            link: The device's open packet link.
            why: Who asked, in one word (the operator).
        """
        self._env = env
        self._node = node
        self._link = link
        self._reason = f"Withdrawal ({why}): the Warden returns to the Hive Stand."

    async def run(self) -> None:
        """Stop the device's Warden, lower the rung and rebuild the Level 0 Warden.

        Raises:
            ColonizeStepError: The packet could not stop the Warden (`withdraw_failed`, or the
                word the order failed with); nothing has changed.
        """
        env, node = self._env, self._node
        step = StepOrder(ColonizeAction.WITHDRAW, WITHDRAW_DEADLINE_S, self._reason)
        reply = await send_order(self._link, node, step)
        if reply.outcome is not ColonizeOutcome.WITHDRAWN:
            raise ColonizeStepError("withdraw_failed")
        event = level_event(env.listener.recorder, node, RuntimeLevel.GATEWAY, self._reason)
        lowered = await env.registry.set_level(node.device_id, RuntimeLevel.GATEWAY, event)
        env.listener.hub.update_node(lowered)
        await dismiss_remote(env.context, env.wardens, node)
        await tell_device(self._link, lowered, "The Warden returned to the Hive Stand.")
        await env.wardens.serve(lowered)  # Same Warden id: the attach is a reconnection.
