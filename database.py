from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from database import get_db, Message

app = FastAPI()

# CORS setup taaki frontend connect ho sake
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. Message save karne ka route (user_id ke sath)
@app.post("/chat")
def save_message(user_id: str, role: str, content: str, db: Session = Depends(get_db)):
    db_message = Message(user_id=user_id, role=role, content=content)
    db.add(db_message)
    db.commit()
    db.refresh(db_message)
    return {"status": "success"}

# 2. Sirf usi user ki chat history lane ka route
@app.get("/history/{user_id}")
def get_chat_history(user_id: str, db: Session = Depends(get_db)):
    messages = db.query(Message).filter(Message.user_id == user_id).all()
    return messages
