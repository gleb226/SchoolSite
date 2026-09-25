import json
import os
import urllib.request

from dotenv import load_dotenv


load_dotenv()

key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
with urllib.request.urlopen(url, timeout=20) as response:
    payload = json.loads(response.read().decode("utf-8"))

for model in payload.get("models", []):
    methods = model.get("supportedGenerationMethods", [])
    if "generateContent" in methods:
        print(model.get("name", "").replace("models/", ""))
