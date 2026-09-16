import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

try:
    models = client.models.list()
    print("Available Models for your key:")
    for model in models.data:
        print("-", model.id)
except Exception as e:
    print("Error fetching models:", e)