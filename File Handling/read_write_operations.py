# reading & writing text files
# Read a prompt from file
with open("prompt.txt", "r", encoding="utf-8") as f:
    prompt = f.read().strip()

# Save AI response to file
response = "Here is the AI answer..."
with open("output.txt", "w", encoding="utf-8") as f:
    f.write(response)

# Append logs without overwriting
with open("log.txt", "a") as f:
    f.write(f"[LOG] {response[:50]}\n")