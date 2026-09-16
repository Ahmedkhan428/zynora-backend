import os
from openai import OpenAI
from dotenv import load_dotenv

# .env file se API key load karne ke liye
load_dotenv()

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

try:
    print("Fetching active models from Groq...\n")
    models = client.models.list()
    
    print("🟢 Active Groq Models List:")
    for model in models.data:
        print(f"-> {model.id}")
except Exception as e:
    print(f"❌ Error aaya: {str(e)}")