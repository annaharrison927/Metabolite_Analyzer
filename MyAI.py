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
        self.client = genai.Client(api_key=self.api_key)

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