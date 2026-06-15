# lists — ordered sequences
models = ["claude-3-5-sonnet", "gpt-4o", "gemini-1.5"]

models.append("llama-3")       # add to end
models.insert(0, "claude-opus") # add at index
first   = models[0]            # index access
sliced  = models[1:3]          # slice [1,3)

for m in models:
    print(f"  - {m}")

print(first, sliced)