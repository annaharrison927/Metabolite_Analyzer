from google import genai
from google.genai import types
import os
from dotenv import load_dotenv

load_dotenv()

class MyAI:
    def __init__(self, model_name: str, system_prompt:str):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        self.model_name = model_name
        self.system_prompt = system_prompt

        # Configure automatic retries for 503 and 429 errors
        retry_config = types.HttpRetryOptions(
            attempts=5,  # Try 5 times before giving up
            initial_delay=2.0,  # Start with a 2-second wait
            max_delay=60.0,  # Never wait more than a minute
            exp_base=2.0  # Double the wait time after each failure
        )

        # Apply these options to the client
        self.client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(retry_options=retry_config)
        )

    def generate_response(self, input_text: str):
        config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            temperature=0.3
        )

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=input_text,
            config=config
        )
        return response.text