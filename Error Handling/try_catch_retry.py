import requests, os
from requests.exceptions import Timeout, HTTPError
from dotenv import load_dotenv

load_dotenv()

URL = "https://api.groq.com/openai/v1/chat/completions"
model = "llama-3.1-8b-instant"
headers = {
    "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
    "Content-Type": "application/json"
}

def safe_call(prompt):
    res = requests.post(
        URL, 
        headers=headers,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=30        
    )

    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"]

def call_with_retry(prompt, retries=3, backoff = 2):
    for attempt in range(retries):
        try:
            result = safe_call(prompt)
            print("Attempt", attempt + 1)
            return result
        except Timeout:
            print("Request timed out")
            wait = backoff ** attempt
            print(f"Attempt {attempt+1} timed out — retrying in {wait}s")
            time.sleep(wait)
        except HTTPError as e:
            print(f"HTTP Error: {e.response.status_code} — {e.response.reason}")
            if e.response.status_code == 429:   # rate limited
                wait = backoff ** attempt
                print(f"Rate limit hit — retrying in {wait}s")
                time.sleep(wait)
            else:
                raise e  # other HTTP errors: fail immediately
        except Exception as e:
            print(f"Unexpected error: {e}")
            print(f"Attempt {attempt+1} failed: {e}")
            time.sleep(backoff ** attempt)

    raise RuntimeError("All retries exhausted")

prompt = input("You: ")
result = call_with_retry(prompt)

print("\nAI:", result)

