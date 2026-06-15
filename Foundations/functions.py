def build_message(role: str, content: str) -> dict:
    """Create a single chat message dict."""
    return {"role": role, "content": content}

def chat(prompt: str, model: str = "claude-sonnet-4") -> list:
    """Return messages list ready for API."""
    return [build_message("user", prompt)]

msgs = chat("Explain transformers")
print(msgs)
