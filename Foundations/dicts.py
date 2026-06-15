# dicts — key-value maps
# Perfect for API request bodies
payload = {
    "model": "claude-sonnet-4",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hi!"}]
}

payload["temperature"] = 0.5   # add key
model = payload.get("model", "default")  # safe get
for k, v in payload.items():
    print(f"{k}: {v}")

print(model)
