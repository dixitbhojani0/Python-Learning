import json
from pathlib import Path

# Load model config
config_path = Path("config.json")
with config_path.open() as f:
    config = json.load(f)          # dict

# Save conversation history
history = [{"role": "user", "content": "hello"}]
with open("history.json", "w") as f:
    json.dump(history, f, indent=4)  # pretty print

# Parse JSON string from API
raw = '{"choices": [{"text": "Hi"}]}'
data = json.loads(raw)             # str → dict
print(data)