import os
import base64
import binascii
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, text
from dotenv import load_dotenv
from groq import Groq
from database import Message, SessionLocal, UserEntitlement

load_dotenv(Path(__file__).with_name(".env"))

app = FastAPI()

# CORS Middleware enable karna taake frontend se requests block na hon
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qlpuxafdmyckbjdjqehv.supabase.co")
SUPABASE_PUBLIC_KEY = os.getenv(
    "SUPABASE_ANON_KEY",
    "sb_publishable_1rp4OzJlTzr-rK3IhU9jig_2M44_W8Y",
)
bearer_scheme = HTTPBearer(auto_error=False)
MESSAGE_LIMIT = 100
IMAGE_LIMIT = 15
USAGE_WINDOW = timedelta(hours=24)

async def get_chat_owner(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    device_id: str | None = Header(default=None, alias="X-Device-ID"),
) -> tuple[str, str]:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Sign in to access chat history")
    if not device_id:
        raise HTTPException(status_code=400, detail="A device ID is required")
    try:
        normalized_device_id = str(UUID(device_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="The device ID is invalid") from None

    try:
        async with httpx.AsyncClient(timeout=8) as auth_client:
            response = await auth_client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "apikey": SUPABASE_PUBLIC_KEY,
                    "Authorization": f"Bearer {credentials.credentials}",
                },
            )
    except httpx.RequestError as error:
        print("Supabase auth verification error:", str(error))
        raise HTTPException(status_code=503, detail="Could not verify your sign-in") from error

    if response.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Your sign-in has expired. Please sign in again")
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not verify your sign-in")
    try:
        owner_id = response.json()["id"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=401, detail="Could not verify your account") from None
    return str(owner_id), normalized_device_id

class ChatRequest(BaseModel):
    chat_id: str
    message: str = ""
    image_data: str | None = None

class RedeemRequest(BaseModel):
    code: str

def get_usage_snapshot(db, owner_id: str) -> dict:
    cutoff = datetime.utcnow() - USAGE_WINDOW
    messages_query = db.query(Message).filter(
        Message.owner_id == owner_id,
        Message.role == "user",
        Message.created_at >= cutoff,
    )
    chat_count = messages_query.count()
    image_count = messages_query.filter(Message.is_image.is_(True)).count()
    oldest_message = messages_query.order_by(Message.created_at.asc()).first()
    oldest_image = messages_query.filter(Message.is_image.is_(True)).order_by(
        Message.created_at.asc()
    ).first()
    entitlement = db.query(UserEntitlement).filter(
        UserEntitlement.owner_id == owner_id
    ).first()
    return {
        "chat_count": chat_count,
        "chat_limit": MESSAGE_LIMIT,
        "image_count": image_count,
        "image_limit": IMAGE_LIMIT,
        "unlimited": bool(entitlement and entitlement.unlimited),
        "chat_resets_at": (oldest_message.created_at + USAGE_WINDOW).isoformat() if oldest_message else None,
        "image_resets_at": (oldest_image.created_at + USAGE_WINDOW).isoformat() if oldest_image else None,
    }

@app.get("/usage")
async def get_usage(owner: tuple[str, str] = Depends(get_chat_owner)):
    db = SessionLocal()
    try:
        return get_usage_snapshot(db, owner[0])
    except Exception as e:
        print("Usage Error:", str(e))
        raise HTTPException(status_code=500, detail="Could not load usage") from e
    finally:
        db.close()

@app.post("/chat")
async def chat_endpoint(data: ChatRequest, owner: tuple[str, str] = Depends(get_chat_owner)):
    if client is None:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY is not configured")
    if not data.message.strip() and not data.image_data:
        raise HTTPException(status_code=400, detail="Add a message or choose an image")

    user_message = data.message.strip() or "Describe this image."
    if data.image_data:
        if len(data.image_data) > 7_000_000:
            raise HTTPException(status_code=413, detail="Image must be 5 MB or smaller")
        try:
            header, encoded_image = data.image_data.split(",", 1)
            if header not in {
                "data:image/jpeg;base64",
                "data:image/png;base64",
                "data:image/webp;base64",
            }:
                raise ValueError("Unsupported image format")
            base64.b64decode(encoded_image, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(status_code=400, detail="Choose a valid JPG, PNG, or WebP image") from None

    try:
        db = SessionLocal()
        try:
            db.execute(text("BEGIN IMMEDIATE"))
            usage = get_usage_snapshot(db, owner[0])
            if not usage["unlimited"]:
                if usage["chat_count"] >= MESSAGE_LIMIT:
                    raise HTTPException(
                        status_code=429,
                        detail="You have reached the 100-message limit for the last 24 hours.",
                    )
                if data.image_data and usage["image_count"] >= IMAGE_LIMIT:
                    raise HTTPException(
                        status_code=429,
                        detail="You have reached the 15-image limit for the last 24 hours.",
                    )
            db.add(Message(
                user_id=data.chat_id,
                owner_id=owner[0],
                device_id=owner[1],
                is_image=bool(data.image_data),
                role="user",
                content=user_message,
            ))
            db.commit()
        finally:
            db.close()

        user_content = user_message
        model = "openai/gpt-oss-120b"
        if data.image_data:
            model = "qwen/qwen3.8-27b"
            user_content = [
                {"type": "text", "text": user_message},
                {"type": "image_url", "image_url": {"url": data.image_data}},
            ]
        completion_options = {"max_tokens": 768} if data.image_data else {}

        completion = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Reply only in English or Roman Urdu (Urdu written using Latin letters). "
                        "Match the user's language when possible. Never use Urdu or Arabic script, "
                        "and do not reply in any other language. If asked to use another language, "
                        "briefly explain in English or Roman Urdu that you can only reply in English "
                           "or Roman Urdu. Keep answers concise and easy to scan. Use short paragraphs; "
                           "for multi-part answers, use brief headings and clear bullets or numbered steps. "
                        "Use a few relevant emojis as visual signposts in headings or key points when natural, "
                        "but never add one to every sentence. Avoid dense text, excessive headings, and repetition."
                    ),
                },
                {"role": "user", "content": user_content}
            ],
            **completion_options,
        )
        ai_reply = completion.choices[0].message.content

        db = SessionLocal()
        try:
            db.add(Message(
                user_id=data.chat_id,
                owner_id=owner[0],
                device_id=owner[1],
                role="assistant",
                content=ai_reply,
            ))
            db.commit()
        finally:
            db.close()

        return {"response": ai_reply}
    except HTTPException:
        raise
    except Exception as e:
        print("Chat Error:", str(e))
        raise HTTPException(status_code=500, detail="The AI request failed") from e

@app.get("/sessions")
async def get_sessions(owner: tuple[str, str] = Depends(get_chat_owner)):
    db = SessionLocal()
    try:
        data = db.query(Message.user_id, Message.content, Message.created_at, Message.id).filter(
            Message.owner_id == owner[0],
            Message.device_id == owner[1],
        ).order_by(
            Message.created_at.asc(), Message.id.asc()
        ).all()
        sessions_dict = {}
        for row in data:
            uid = row.user_id
            if uid not in sessions_dict:
                sessions_dict[uid] = {
                    "user_id": uid,
                    "title": row.content[:25] + "..." if len(row.content) > 25 else row.content,
                    "last_activity": (row.created_at, row.id),
                }
            else:
                sessions_dict[uid]["last_activity"] = (row.created_at, row.id)
        
        recent_sessions = sorted(
            sessions_dict.values(),
            key=lambda item: item["last_activity"],
            reverse=True,
        )
        for item in recent_sessions:
            item.pop("last_activity")
        return recent_sessions
    except Exception as e:
        print("Sessions Error:", str(e))
        raise HTTPException(status_code=500, detail="Could not load sessions") from e
    finally:
        db.close()

@app.get("/history/{session_id}")
async def get_history(session_id: str, owner: tuple[str, str] = Depends(get_chat_owner)):
    db = SessionLocal()
    try:
        messages = db.query(Message.role, Message.content).filter(
            Message.user_id == session_id,
            Message.owner_id == owner[0],
            Message.device_id == owner[1],
        ).order_by(Message.created_at.asc()).all()
        return [{"role": message.role, "content": message.content} for message in messages]
    except Exception as e:
        print("History Error:", str(e))
        raise HTTPException(status_code=500, detail="Could not load chat history") from e
    finally:
        db.close()

@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, owner: tuple[str, str] = Depends(get_chat_owner)):
    db = SessionLocal()
    try:
        db.query(Message).filter(
            Message.user_id == session_id,
            Message.owner_id == owner[0],
            Message.device_id == owner[1],
        ).delete(synchronize_session=False)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        print("Delete Session Error:", str(e))
        raise HTTPException(status_code=500, detail="Could not delete session") from e
    finally:
        db.close()

@app.post("/redeem")
async def redeem_code(data: RedeemRequest, owner: tuple[str, str] = Depends(get_chat_owner)):
    if data.code.strip().upper() not in {"KHAN", "FREE"}:
        raise HTTPException(status_code=400, detail="Invalid Redeem Code")
    db = SessionLocal()
    try:
        entitlement = db.query(UserEntitlement).filter(
            UserEntitlement.owner_id == owner[0]
        ).first()
        if entitlement is None:
            entitlement = UserEntitlement(owner_id=owner[0], unlimited=True)
            db.add(entitlement)
        else:
            entitlement.unlimited = True
        db.commit()
        return {"message": "Unlimited access activated for this account!", "unlimited": True}
    except Exception as e:
        db.rollback()
        print("Redeem Error:", str(e))
        raise HTTPException(status_code=500, detail="Could not activate unlimited access") from e
    finally:
        db.close()