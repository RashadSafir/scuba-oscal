import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(base_url=os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/") + "/openai/v1/",
                api_key=os.environ["AZURE_OPENAI_API_KEY"])
r = client.chat.completions.create(model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
                                   messages=[{"role": "user", "content": "Reply with: pong"}])
print(r.choices[0].message.content)
