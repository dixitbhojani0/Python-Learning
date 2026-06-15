# variables & types

# Python infers types automatically
model_name  = "claude-sonnet-4"    # str
max_tokens  = 1024                  # int
temperature = 0.7                   # float
streaming   = True                  # bool

# f-strings for readable output
print(f"Using {model_name} at temp={temperature}")