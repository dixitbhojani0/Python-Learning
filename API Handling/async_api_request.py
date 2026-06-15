import httpx, asyncio, os
from dotenv import load_dotenv

load_dotenv()

headers = {
    "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
    "Content-Type": "application/json"
}

async def call_groq(prompt: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"]

    except httpx.TimeoutException:
        print("Request timed out")
    except httpx.HTTPStatusError as e:
        print(f"HTTP error {e.response.status_code}: {e.response.json()}")
    except Exception as e:
        print(f"Unexpected error: {e}")

async def main():
    print("Async Chat. Type 'quit' to exit.\n")
    while True:
        prompt = input("You: ")
        if prompt.lower() in ["quit", "exit", "q"]:
            print("Bye!")
            break
        answer = await call_groq(prompt)
        if answer:
            print(f"\nAI: {answer}\n")

asyncio.run(main())