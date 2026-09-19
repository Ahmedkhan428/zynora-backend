import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from groq import Groq
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

# Groq Client Initialization
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# 1. Chat aur AI Response ka route (user_id ke sath save hoga)
@app.post("/chat")
def chat_endpoint(user_id: str, message: str, db: Session = Depends(get_db)):
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    
    # User ka message database mein save karein
    user_msg = Message(user_id=user_id, role="user", content=message)
    db.add(user_msg)
    db.commit()

    # Groq AI se response lein
    try:
        completion = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": message}]
        )
        ai_response = completion.choices[0].message.content
    except Exception as e:
        ai_response = "Sorry, I am having trouble connecting to AI right now."

    # AI ka response bhi usi user_id ke sath save karein
    ai_msg = Message(user_id=user_id, role="assistant", content=ai_response)
    db.add(ai_msg)
    db.commit()

    return {"response": ai_response}

# 2. Sirf usi user ki chat history lane ka route
@app.get("/history/{user_id}")
def get_chat_history(user_id: str, db: Session = Depends(get_db)):
    messages = db.query(Message).filter(Message.user_id == user_id).all()
    return messages
