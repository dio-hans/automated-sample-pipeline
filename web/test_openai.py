import os
from dotenv import load_dotenv
from openai import OpenAI

# 1. Load your secret key from the .env configuration file
load_dotenv()

# 2. Connect to OpenAI using that loaded key
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)

# 3. Send a test message to the AI
print("Sending test request to OpenAI...")
response = client.chat.completions.create(
    model="gpt-4o-mini", # The cheapest, fastest model for testing
    messages=[
        {"role": "user", "content": "Write a 3-word sentence celebrating that my code works."}
    ]
)

# 4. Print out the AI's reply
print("\nAI Response:")
print(response.choices[0].message.content)
