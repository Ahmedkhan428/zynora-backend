import os
import time
import json
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from groq import Groq
from sqlmodel import SQLModel, Field, create_engine, Session, select
from typing import Optional

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Single Groq Client
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# SQLite Database Setup
DATABASE_URL = "sqlite:///./zynora.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

class ChatSession(SQLModel, table=True):
    session_id: str = Field(primary_key=True)
    title: str

class ChatMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str
    role: str
    content: str
    image: Optional[str] = None

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

rate_limit_db = {}
vip_users_db = set()
VALID_CODES = {"khan"}

MAX_REQUESTS = 6
TIME_WINDOW = 24 * 60 * 60  # 24 hours in seconds

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    image: str | None = None

class RedeemRequest(BaseModel):
    code: str

SYSTEM_INSTRUCTION = """
You are Zynora AI, an intelligent, helpful, and friendly AI assistant.
"""

@app.post("/chat")
def chat(req: ChatRequest, request: Request):
    s_id = req.session_id
    try:
        client_ip = request.client.host if request.client else "unknown"
        current_time = time.time()

        is_vip = client_ip in vip_users_db

        if not is_vip:
            if client_ip not in rate_limit_db:
                rate_limit_db[client_ip] = []

            rate_limit_db[client_ip] = [
                t for t in rate_limit_db[client_ip] if current_time - t < TIME_WINDOW
            ]

            if len(rate_limit_db[client_ip]) >= MAX_REQUESTS:
                oldest_request = rate_limit_db[client_ip][0]
                remaining_seconds = TIME_WINDOW - (current_time - oldest_request)
                remaining_hours = round(remaining_seconds / 3600, 1)
                
                limit_msg = f"⏳ **Limit Reached!** Aap 24 ghante mein sirf 6 messages bhej sakte hain. Meharbani karke **{remaining_hours} ghante** intezaar karein ya redeem code use karein."
                return {
                    "reply": limit_msg,
                    "session_id": s_id or "limit_session"
                }

        user_msg = req.message
        user_image = req.image

        with Session(engine) as db_session:
            if not s_id:
                session_count = len(db_session.exec(select(ChatSession)).all())
                s_id = f"session_{session_count + 1}"
                title = user_msg[:25] + "..." if len(user_msg) > 25 else user_msg
                db_session.add(ChatSession(session_id=s_id, title=title))
                db_session.commit()
            else:
                existing_session = db_session.get(ChatSession, s_id)
                if not existing_session:
                    title = user_msg[:25] + "..." if len(user_msg) > 25 else user_msg
                    db_session.add(ChatSession(session_id=s_id, title=title))
                    db_session.commit()

            db_session.add(ChatMessage(session_id=s_id, role="user", content=user_msg, image=user_image))
            db_session.commit()

            db_messages = db_session.exec(select(ChatMessage).where(ChatMessage.session_id == s_id)).all()

            messages_payload = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
            for m in db_messages:
                role = "user" if m.role == "user" else "assistant"
                messages_payload.append({"role": role, "content": str(m.content)})

        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages_payload,
            temperature=0.7,
            max_tokens=1024
        )

        ai_reply = completion.choices[0].message.content if completion.choices else "API se koi text nahi mila."
        
        with Session(engine) as db_session:
            db_session.add(ChatMessage(session_id=s_id, role="assistant", content=ai_reply))
            db_session.commit()

        if not is_vip:
            rate_limit_db[client_ip].append(current_time)

        return {
            "reply": ai_reply,
            "session_id": s_id
        }
    except Exception as e:
        error_msg = f"Backend Error: {str(e)}"
        print(error_msg)
        return {
            "reply": error_msg,
            "session_id": s_id if 's_id' in locals() and s_id else "error_session"
        }

@app.post("/redeem")
def redeem_code(req: RedeemRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if req.code in VALID_CODES:
        vip_users_db.add(client_ip)
        return {"success": True, "message": "🎉 Mubarak ho! Code successfully redeem ho gaya hai."}
    else:
        raise HTTPException(status_code=400, detail="❌ Invalid Code! Ghalat code enter kiya gaya hai.")

@app.get("/sessions")
def get_sessions():
    with Session(engine) as db_session:
        sessions = db_session.exec(select(ChatSession)).all()
        # Frontend ke liye 'id' key return ki hai taaki match ho sakay
        return [{"id": s.session_id, "title": s.title} for s in sessions[::-1]]

@app.get("/sessions/{session_id}")
def get_session_messages(session_id: str):
    with Session(engine) as db_session:
        messages = db_session.exec(select(ChatMessage).where(ChatMessage.session_id == session_id)).all()
        # Frontend role ko 'user' aur 'assistant' expect karta hai
        formatted_msgs = []
        for m in messages:
            r = "user" if m.role == "user" else "assistant"
            formatted_msgs.append({"role": r, "content": m.content})
        return {"messages": formatted_msgs}

@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    with Session(engine) as db_session:
        messages = db_session.exec(select(ChatMessage).where(ChatMessage.session_id == session_id)).all()
        for msg in messages:
            db_session.delete(msg)
        
        session_obj = db_session.get(ChatSession, session_id)
        if session_obj:
            db_session.delete(session_obj)
            db_session.commit()
            return {"success": True, "message": "Session successfully deleted"}
        else:
            raise HTTPException(status_code=404, detail="Session not found")