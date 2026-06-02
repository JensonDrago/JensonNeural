from fastapi import FastAPI

app = FastAPI(title="HostIA AWS GraphRAG")

@app.get("/health")
def health():
    return {"status": "ok", "service": "hostia-aws-graphrag"}

@app.post("/chat")
def chat(payload: dict):
    question = payload.get("question", "")
    return {
        "question": question,
        "answer": "GraphRAG AWS backend pendiente de implementación.",
    }