# read_jsonl.py
import json

def read_jsonl(path: str):
    """Generator — yields one dict per line."""
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:           # skip blank lines
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Bad JSON on line {line_num}: {e}")

# Usage
for record in read_jsonl("prompts.jsonl"):
    print(record["id"], record["prompt"])