import argparse, os, requests
from dotenv import load_dotenv

load_dotenv()

URL = "https://api.groq.com/openai/v1/chat/completions"
headers = {
    "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
    "Content-Type": "application/json"
}

def ask_groq(prompt: str, model: str, tokens: int) -> str:
    try:
        res = requests.post(
            URL,
            headers=headers,
            json={
                "model": model,
                "max_tokens": tokens,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Error: {e}")
        return None

def main():
    try:
        parser = argparse.ArgumentParser(description="Ask Groq model via CLI")
        parser.add_argument("prompt", type=str, help="Your question for the model")
        parser.add_argument("--model", type=str, default="llama-3.1-8b-instant")
        parser.add_argument("--tokens", type=int, default=1024)

        args = parser.parse_args()
        print("\nAI:", ask_groq(args.prompt, args.model, args.tokens))
    except Exception as e:
        print(f"Unexpected Error: {e}")

if __name__ == "__main__":
    main()
