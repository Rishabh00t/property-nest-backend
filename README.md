# PropCore Chatbot Backend

FastAPI backend for the chatbot used by the PropCore frontend.

## Structure

```text
backend/
  app/
    main.py
    api/
      router.py
      endpoints/
        bot.py
        personalized_bot.py
    core/
      config.py
      prompts.py
    schemas/
      chat.py
      property_chat.py
    services/
      chat_service.py
      property_chat_service.py
    utils/
      connection_manager.py
  requirements.txt
  .env.example
```

## Run

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

## WebSocket

`ws://localhost:8001/ws/chat`

## REST

`POST /api/chat`

## Personalized Property Chat

This backend also supports a property-specific chatbot that creates and resumes rows in `public.leads` using the property/project ID, name, phone number, and optional email.

### Required environment variables

```bash
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
```

### Endpoints

`POST /api/property-chat/leads`

`POST /api/property-chat/leads/{lead_id}/messages`

`WS /ws/property-chat/{lead_id}`
