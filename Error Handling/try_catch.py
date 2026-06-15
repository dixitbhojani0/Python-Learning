import requests, os
from requests.exceptions import HTTPError, Timeout
from dotenv import load_dotenv

load_dotenv()

URL = "https://api.groq.com/openai/v1/chat/completions"

headers = {
    "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
    "Content-Type": "application/json"
}

def safe_call(prompt: str) -> str | None:
    try:
        print("Sending request...")
        res = requests.post(
            URL,
            headers=headers,
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]

    except Timeout:
        print("Request timed out after 30s")

    except HTTPError as e:
        error = e.response.json().get("error", {})
        print(f"HTTP {e.response.status_code}: {error.get('message', e)}")

    except Exception as e:
        print(f"Unexpected error: {e}")

    finally:
        print("Request attempt complete")  # always runs, success or fail

    return None


# Run it
prompt = input("You: ")
result = safe_call(prompt)

if result:
    print(f"\nAI: {result}")
else:
    print("No response received")