import chromadb
from chromadb.config import Settings
from config import CHROMA_DB_PATH, CHROMA_COLLECTION

client = chromadb.PersistentClient(
    path=CHROMA_DB_PATH,
    settings=Settings(anonymized_telemetry=False),
)
collection = client.get_collection(CHROMA_COLLECTION)

total = collection.count()
print(f"\n{'='*50}")
print(f"  Collection : {CHROMA_COLLECTION}")
print(f"  Total chunks stored : {total}")
print(f"{'='*50}\n")

if total == 0:
    print("No documents indexed yet.")
else:
    results = collection.get(include=["documents", "metadatas"])

    # Group by source file
    sources: dict[str, list] = {}
    for doc, meta in zip(results["documents"], results["metadatas"]):
        src = meta["source"]
        sources.setdefault(src, []).append((meta["chunk_index"], doc))

    for src, chunks in sorted(sources.items()):
        print(f"📄 {src}  ({len(chunks)} chunks)")
        for idx, text in sorted(chunks)[:2]:  # preview first 2 chunks
            print(f"   chunk #{idx}: {text[:120]}...")
        if len(chunks) > 2:
            print(f"   ... and {len(chunks) - 2} more chunks")
        print()
