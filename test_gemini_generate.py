"""Quick test to check which Gemini models are reachable."""
import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
if not key:
    print("ERROR: GEMINI_API_KEY not set in .env")
    exit(1)

if not key.startswith("AIza"):
    print(f"WARNING: Key starts with '{key[:8]}...' — expected 'AIza...'")
    print("Get a valid key from https://aistudio.google.com/app/apikey")

models = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]
payload = json.dumps({"contents": [{"parts": [{"text": "Return only: OK"}]}]}).encode("utf-8")

print(f"\nTesting API key: {key[:8]}...{key[-4:]}\n")
for model in models:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            print(f"  OK  {model} (status {response.status})")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="ignore")
        print(f"  {error.code} {model}: {body[:100].replace(chr(10), ' ')}")
    except Exception as error:
        print(f"  ERR {model}: {type(error).__name__}: {str(error)[:80]}")
