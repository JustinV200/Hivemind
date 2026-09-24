"""Serve the chat resource: read the conversation with the Queen, and write to her.

The chat is the human end of the Queen's inbox (ADR-0032): ``GET /v1/chat`` reads it a page at a
time by the log's own position (``after`` to read on from a cursor, ``before`` to scroll back,
neither for the newest page), and ``POST /v1/chat`` appends the human's message and wakes the
Queen, who reads it on her next tick as a ``HumanMessage`` her Attendant scores. Every line is
``C2``, so reading needs ``honey:clearance:c2`` beside ``entrance:submit``; the words never reach
the trail. New lines stream on ``/v1/chat/stream`` (``hivemind.entrance.streams``).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Registered in
    the route table. Calls into the Queen's door (writes) and her chat log (reads).

Key invariants:
    - Reading never changes the chat; writing goes through the Queen alone.

See Also:
    - hivemind.queen.chat for the chat log and the door.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.models import ChatAccepted, ChatPage, ChatPost, chat_line
from hivemind.queen import ChatQuery
from hivemind.queen.chat import DEFAULT_CHAT_PAGE, MAX_CHAT_PAGE

SUBMIT = "entrance:submit"  # Reading and writing the chat is part of giving the Hive work.

__all__ = ["ROUTES"]


async def read_chat(
    services: Services,
    after: Annotated[int | None, Query(ge=0, description="Only lines after this position.")] = None,
    before: Annotated[
        int | None, Query(ge=1, description="Only lines before this position: scroll back.")
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_CHAT_PAGE, description="Most lines to return.")
    ] = DEFAULT_CHAT_PAGE,
) -> ChatPage:
    """Read a page of the chat, oldest first.

    Args:
        services: The Entrance's services (the chat log).
        after: Only lines after this position; the page then starts there.
        before: Only lines before this position.
        limit: Most lines to return.

    Returns:
        The page, and its newest position.
    """
    query = ChatQuery(after_seq=after, before_seq=before, limit=limit)
    # Latency: one local indexed read of the Queen's chat table.
    entries = [chat_line(entry) for entry in await services.hive.chat.read(query)]
    newest = entries[-1].seq if entries else None
    return ChatPage(entries=entries, newest_seq=newest)


async def post_chat(body: ChatPost, caller: CallerParam, services: Services) -> ChatAccepted:
    """Append the human's message to the chat and wake the Queen.

    Args:
        body: The words, and the task they concern.
        caller: The admitted caller (the message names its device).
        services: The Entrance's services (the Queen's door).

    Returns:
        The chat line's id, once it is committed.
    """
    entry_id = await services.queen.post_human_message(body.text, caller.device.id, body.task_id)
    return ChatAccepted(id=entry_id)


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/chat",
        listeners=BOTH_LISTENERS,
        access=session_with(SUBMIT, c2=True),
        effect=RouteEffect.READ,
        endpoint=read_chat,
        summary="Read a page of the chat with the Queen (C2).",
        response_model=ChatPage,
    ),
    RouteSpec(
        method="POST",
        path="/v1/chat",
        listeners=BOTH_LISTENERS,
        access=session_with(SUBMIT),
        effect=RouteEffect.INBOX,
        endpoint=post_chat,
        summary="Send the Queen a message; it enters her inbox.",
        status_code=202,
        response_model=ChatAccepted,
    ),
)
