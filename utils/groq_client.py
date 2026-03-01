import os
import logging
from groq import AsyncGroq

logger = logging.getLogger("ghost_tracker")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Initialize the async client
try:
    client = AsyncGroq(api_key=GROQ_API_KEY)
except Exception as e:
    logger.error(f"Failed to initialize Groq client: {e}")
    client = None


async def generate_response(messages: list) -> str:
    """
    Sends a conversation thread to the Groq API and returns the text response.
    """
    if not client:
        return "❌ Warning: Groq API client is offline. Check your GROQ_API_KEY in the .env file."

    try:
        response = await client.chat.completions.create(
            model="llama3-70b-8192", messages=messages, temperature=0.7, max_tokens=1024
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq API Generation Error: {e}")
        return f"❌ AI Neural Failure: {str(e)}"
