import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()

class MyAI:
    def __init__(self, model_name):
        self.model_name = model_name
        self.api_key = os.environ.get("GEMINI_API_KEY")

        genai.configure(api_key=self.api_key)

        self.model = genai.GenerativeModel(self.model_name)

    def generate_response(self, input_text: str):
        response = self.model.generate_content(input_text)
        return response