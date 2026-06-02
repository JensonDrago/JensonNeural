"""
Carga rag_malla.jsonl en Neo4j como nodos :RagDocument y los enlaza con :MallaObject.

Por defecto carga sin embeddings. Para busqueda semantica, usar --with-embeddings.

Ejemplos:
  python D:\Jenson\HostIA\load_rag_malla_to_neo4j.py --password TU_PASSWORD
  python D:\Jenson\HostIA\load_rag_malla_to_neo4j.py --password TU_PASSWORD --with-embeddings --openai-api-key sk-...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from neo4j import GraphDatabase


DEFAULT_JSONL = Path(r"D:\Jenson\HostIA\malla_neo4j_csv_multi\rag_malla.jsonl")
DEFAULT_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEFAULT_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")


CREATE_CONSTRAINTS = [
    "CREATE CONSTRAINT rag_document_id IF NOT EXISTS FOR (d:RagDocument) REQUIRE d.id IS UNIQUE",
]

CREATE_VECTOR_INDEX = """
CREATE VECTOR INDEX rag_cobol_index IF NOT EXISTS
FOR (d:RagDocument) ON (d.embedding)
OPTIONS {indexConfig: {
  `vector.dimensions`: 1536,
  `vector.similarity_function`: 'cosine'
}}
"""

MERGE_RAGDOC_WITHOUT_EMBEDDING = """
MERGE (d:RagDocument {id: $id})
SET d.text = $text,
    d.type = $type,
    d.source = $source,
    d.object_id = $object_id,
    d.malla_id = $malla_id,
    d.memname = $memname,
    d.object_type = $object_type
"""

MERGE_RAGDOC_WITH_EMBEDDING = MERGE_RAGDOC_WITHOUT_EMBEDDING + ",\n    d.embedding = $embedding\n"

LINK_MALLA_OBJECT = """
MATCH (d:RagDocument {id: $doc_id})
MATCH (m:MallaObject {object_id: $object_id})
MERGE (d)-[:DOCUMENTS]->(m)
"""


def read_jsonl(path: Path) -> list[dict]:
    docs = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                docs.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON invalido en {path}, linea {line_no}: {exc}") from exc
    return docs


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def get_embeddings(texts: list[str], api_key: str, base_url: str, model: str) -> list[list[float]]:
    resp = requests.post(
        f"{base_url.rstrip('/')}/embeddings",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": model, "input": texts},
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    data.sort(key=lambda item: item["index"])
    return [item["embedding"] for item in data]


def doc_params(doc: dict, embedding: list[float] | None = None) -> dict:
    meta = doc.get("metadata", {})
    return {
        "id": doc["id"],
        "text": doc.get("text", ""),
        "type": meta.get("type", "malla_object"),
        "source": meta.get("source_ref") or meta.get("source_file", ""),
        "object_id": meta.get("object_id", ""),
        "malla_id": meta.get("malla_id", ""),
        "memname": meta.get("memname", ""),
        "object_type": meta.get("object_type", ""),
        "embedding": embedding,
    }


def load_docs(session, docs: list[dict], embeddings: list[list[float]] | None = None) -> tuple[int, int]:
    loaded = 0
    linked = 0
    for idx, doc in enumerate(docs):
        emb = embeddings[idx] if embeddings else None
        params = doc_params(doc, emb)
        session.run(MERGE_RAGDOC_WITH_EMBEDDING if embeddings else MERGE_RAGDOC_WITHOUT_EMBEDDING, **params)

        if params["object_id"]:
            result = session.run(LINK_MALLA_OBJECT, doc_id=params["id"], object_id=params["object_id"])
            summary = result.consume()
            linked += summary.counters.relationships_created
        loaded += 1
    return loaded, linked


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga rag_malla.jsonl como RagDocument en Neo4j.")
    parser.add_argument("--jsonl", default=str(DEFAULT_JSONL), help="Ruta del rag_malla.jsonl.")
    parser.add_argument("--uri", default=DEFAULT_URI, help="URI Neo4j.")
    parser.add_argument("--user", default=DEFAULT_USER, help="Usuario Neo4j.")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Password Neo4j. Tambien puede venir en NEO4J_PASSWORD.")
    parser.add_argument("--batch-size", type=int, default=50, help="Tamanio de lote.")
    parser.add_argument("--with-embeddings", action="store_true", help="Genera embeddings con OpenAI.")
    parser.add_argument("--openai-api-key", default=DEFAULT_OPENAI_API_KEY, help="API key OpenAI. Tambien puede venir en OPENAI_API_KEY.")
    parser.add_argument("--openai-base-url", default=DEFAULT_OPENAI_BASE_URL, help="Base URL OpenAI.")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBED_MODEL, help="Modelo de embeddings.")
    args = parser.parse_args()

    jsonl_path = Path(args.jsonl)
    if not jsonl_path.exists():
        print(f"ERROR: no existe {jsonl_path}")
        sys.exit(1)
    if not args.password:
        print("ERROR: falta password Neo4j. Usa --password o variable NEO4J_PASSWORD.")
        sys.exit(1)
    if args.with_embeddings and not args.openai_api_key:
        print("ERROR: falta OpenAI API key. Usa --openai-api-key o variable OPENAI_API_KEY.")
        sys.exit(1)

    docs = read_jsonl(jsonl_path)
    print(f"Documentos leidos: {len(docs)} desde {jsonl_path}")
    if not docs:
        print("No hay documentos para cargar.")
        return

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    try:
        driver.verify_connectivity()
    except Exception as exc:
        print(f"ERROR conectando a Neo4j: {exc}")
        sys.exit(1)

    total_loaded = 0
    total_linked = 0
    with driver.session() as session:
        for stmt in CREATE_CONSTRAINTS:
            session.run(stmt).consume()
        if args.with_embeddings:
            session.run(CREATE_VECTOR_INDEX).consume()

        batches = list(chunks(docs, args.batch_size))
        for num, batch in enumerate(batches, start=1):
            print(f"Lote {num}/{len(batches)} ({len(batch)} docs)...", end=" ", flush=True)
            embeddings = None
            if args.with_embeddings:
                texts = [doc.get("text", "")[:8000] for doc in batch]
                embeddings = get_embeddings(texts, args.openai_api_key, args.openai_base_url, args.embedding_model)
                time.sleep(0.2)

            loaded, linked = load_docs(session, batch, embeddings)
            total_loaded += loaded
            total_linked += linked
            print("OK")

    driver.close()
    print("\nCarga completada")
    print(f"RagDocument cargados/actualizados: {total_loaded}")
    print(f"Relaciones DOCUMENTS nuevas: {total_linked}")
    print("\nValidacion sugerida en Neo4j Browser:")
    print("MATCH (d:RagDocument {type:'malla_object'}) RETURN count(d) AS docs;")
    print("MATCH (d:RagDocument {type:'malla_object'})-[:DOCUMENTS]->(m:MallaObject) RETURN count(*) AS relaciones;")


if __name__ == "__main__":
    main()
