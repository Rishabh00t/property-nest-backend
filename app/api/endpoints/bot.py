from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService
from app.utils.connection_manager import ConnectionManager

chat_router = APIRouter()
manager = ConnectionManager()


@chat_router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    chat_service = ChatService()
    await manager.connect(websocket)
    await websocket.send_text(
        "Welcome to PropCore! I can help you with properties, pricing, bookings, saved homes, and buyer support."
    )

    try:
        while True:
            message = await websocket.receive_text()
            response = chat_service.get_response(message)
            await websocket.send_text(response)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        chat_service.clear_memory()


@chat_router.post("/api/chat", response_model=ChatResponse)
async def rest_chat(payload: ChatRequest):
    response = ChatService().get_response(payload.message)
    return ChatResponse(reply=response)
