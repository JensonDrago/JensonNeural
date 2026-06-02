import os

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

GRAPH_BACKEND = os.getenv("GRAPH_BACKEND", "neptune")
VECTOR_BACKEND = os.getenv("VECTOR_BACKEND", "opensearch")

GRAPH_ENDPOINT = os.getenv("GRAPH_ENDPOINT", "")
GRAPH_PORT = int(os.getenv("GRAPH_PORT", "8182"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

S3_RAW_BUCKET = os.getenv("S3_RAW_BUCKET", "")
S3_CURATED_BUCKET = os.getenv("S3_CURATED_BUCKET", "")
S3_RAG_BUCKET = os.getenv("S3_RAG_BUCKET", "")

TOP_K = int(os.getenv("TOP_K", "8"))