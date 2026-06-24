from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/earthquakes")
async def earthquake_ws(websocket: WebSocket) -> None:
    from app.services.websocket_manager import WebSocketManager

    mgr: WebSocketManager = websocket.app.state.ws_manager  # type: ignore[attr-defined]

    await mgr.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await mgr.disconnect(websocket)
