from groq import Groq
from models import RetrievedChunk
from config import GROQ_API_KEY, LLM_MODEL, LLM_TEMPERATURE, LLM_MAX_TOKENS

_client = Groq(api_key=GROQ_API_KEY)

_SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the question using ONLY the provided context. "
    "If the answer is not in the context, say \"I don't have enough information to answer this.\""
)


def build_prompt(question: str, chunks: list[RetrievedChunk]) -> str:
    context = "\n\n---\n\n".join(
        f"[Source: {c.source}]\n{c.text}" for c in chunks
    )
    return f"Context:\n{context}\n\nQuestion: {question}\n\nAnswer:"


def generate(question: str, chunks: list[RetrievedChunk]) -> tuple[str, str]:
    prompt = build_prompt(question, chunks)
    response = _client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
    )
    return response.choices[0].message.content, prompt
