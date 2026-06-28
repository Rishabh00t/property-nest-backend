from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.schemas.property_chat import (
    PropertyChatMessageRequest,
    PropertyChatResponse,
    PropertyLeadCreateRequest,
    PropertyLeadResponse,
)
from app.services.property_chat_service import PropertyChatService
from app.utils.connection_manager import ConnectionManager

personalized_chat_router = APIRouter()
personalized_manager = ConnectionManager()


@personalized_chat_router.post("/api/property-chat/leads", response_model=PropertyLeadResponse)
async def create_property_chat_lead(payload: PropertyLeadCreateRequest):
    service = PropertyChatService()

    try:
        return service.create_or_resume_lead(payload)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Unable to create chat lead: {error}") from error


@personalized_chat_router.post(
    "/api/property-chat/leads/{lead_id}/messages",
    response_model=PropertyChatResponse,
)
async def send_property_chat_message(lead_id: str, payload: PropertyChatMessageRequest):
    service = PropertyChatService()

    try:
        return service.get_response(lead_id, payload.message)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Unable to generate chat response: {error}") from error


@personalized_chat_router.websocket("/ws/property-chat/{lead_id}")
async def websocket_property_chat(websocket: WebSocket, lead_id: str):
    service = PropertyChatService()

    await websocket.accept()
    try:
        lead = service.get_lead(lead_id)
    except ValueError:
        await websocket.send_text("We could not find that property chat lead.")
        await websocket.close(code=1008)
        return
    except Exception as error:
        await websocket.send_text(f"The personalized chat service is unavailable right now: {error}")
        await websocket.close(code=1011)
        return

    personalized_manager.active_connections.add(websocket)
    await websocket.send_text(
        f"Welcome {lead.name}. I'm here to help with {lead.project_name}."
    )

    try:
        while True:
            message = await websocket.receive_text()
            try:
                response = service.get_response(lead_id, message)
                await websocket.send_text(response.reply)
            except Exception:
                await websocket.send_text(
                    "The personalized chat ran into an unexpected issue. Please try again in a moment."
                )
    except WebSocketDisconnect:
        pass
    finally:
        personalized_manager.disconnect(websocket)
        service.clear_history(lead_id)
