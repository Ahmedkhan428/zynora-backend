from sqlalchemy import Boolean, create_engine, Column, Index, Integer, String, DateTime, Text, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

SQLALCHEMY_DATABASE_URL = "sqlite:///./zynora.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    owner_id = Column(String(36), nullable=True)
    device_id = Column(String(36), nullable=True)
    is_image = Column(Boolean, nullable=False, default=False)
    role = Column(String)         # "user" ya "ai"
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

Index("ix_messages_owner_device_session", Message.owner_id, Message.device_id, Message.user_id)

class UserEntitlement(Base):
    __tablename__ = "user_entitlements"

    owner_id = Column(String(36), primary_key=True)
    unlimited = Column(Boolean, nullable=False, default=False)
    redeemed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

with engine.begin() as connection:
    if "messages" in inspect(engine).get_table_names():
        existing_columns = {column["name"] for column in inspect(engine).get_columns("messages")}
        if "owner_id" not in existing_columns:
            connection.execute(text("ALTER TABLE messages ADD COLUMN owner_id VARCHAR(36)"))
        if "device_id" not in existing_columns:
            connection.execute(text("ALTER TABLE messages ADD COLUMN device_id VARCHAR(36)"))
        if "is_image" not in existing_columns:
            connection.execute(text("ALTER TABLE messages ADD COLUMN is_image BOOLEAN NOT NULL DEFAULT 0"))

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()