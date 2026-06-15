import requests, os
from dotenv import load_dotenv

load_dotenv()
print("GROQ KEY:", os.environ.get("GROQ_API_KEY", "NOT FOUND"))

# headers = {
#     "x-api-key": os.environ["ANTHROPIC_API_KEY"],
#     "anthropic-version": "2023-06-01",
#     "content-type": "application/json",
# }

# body = {
#     "model": os.environ["DEFAULT_MODEL"],
#     "max_tokens": 1024,
#     "messages": [{"role": "user", "content": "Hello!"}]
# }

# try:
#     res = requests.post(
#         "https://api.anthropic.com/v1/messages",
#         headers=headers, json=body, timeout=30
#     )

#     # check for API-level errors before raise_for_status
#     data = res.json()
#     if data.get("type") == "error":
#         error = data["error"]
#         print(f"API Error [{error['type']}]: {error['message']}")
#     else:
#         res.raise_for_status()
#         text = data["content"][0]["text"]
#         print(text)

# except requests.exceptions.Timeout:
#     print("Request timed out")
# except requests.exceptions.HTTPError as e:
#     print(f"HTTP error: {e}")
# except Exception as e:
#     print(f"Unexpected error: {e}")

try:
    user_prompt = input("You: ")        # ask user for input

    res = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": user_prompt}]
        },
        timeout=30
    )
    data = res.json()
    res.raise_for_status()
    print("\nAI:", data["choices"][0]["message"]["content"])

except requests.exceptions.Timeout:
    print("Request timed out")
except requests.exceptions.HTTPError as e:
    print(f"HTTP error: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")