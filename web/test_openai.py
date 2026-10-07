import os
import unittest
from dotenv import load_dotenv
from openai import OpenAI
from django.test import TestCase

load_dotenv()


class OpenAITest(TestCase):
    @unittest.skip("External API test requires active OpenAI quota")
    def test_openai_connection(self):
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        print("Sending test request to OpenAI...")
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": "Write a 3-word sentence celebrating that my code works.",
                }
            ],
        )

        print("\nAI Response:")
        print(response.choices[0].message.content)