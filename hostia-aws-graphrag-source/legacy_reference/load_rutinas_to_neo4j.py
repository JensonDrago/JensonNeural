"""
Ejecuta load_rutinas.cypher para cargar las rutinas estructuradas a Neo4j.

Uso:
  python D:\Jenson\HostIA\load_rutinas_to_neo4j.py --password TU_PASSWORD
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from neo4j import GraphDatabase


DEFAULT_CYPHER = Path(r"D:\Jenson\HostIA\Rutinas_example\rutinas_neo4j_csv\load_rutinas.cypher")
DEFAULT_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD", "")


def split_cypher(script: str) -> list[str]:
    parts = []
    current = []
    in_single = False
    in_double = False
    for ch in script:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        if ch == ";" and not in_single and not in_double:
            stmt = "".join(current).strip()
            if stmt:
                parts.append(stmt)
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return [re.sub(r"(?m)^\s*//.*$", "", p).strip() for p in parts if p.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga CSV de rutinas en Neo4j ejecutando load_rutinas.cypher.")
    parser.add_argument("--cypher", default=str(DEFAULT_CYPHER))
    parser.add_argument("--uri", default=DEFAULT_URI)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    args = parser.parse_args()

    cypher_path = Path(args.cypher)
    if not cypher_path.exists():
        print(f"ERROR: no existe {cypher_path}")
        sys.exit(1)
    if not args.password:
        print("ERROR: falta password Neo4j. Usa --password o NEO4J_PASSWORD.")
        sys.exit(1)

    statements = split_cypher(cypher_path.read_text(encoding="utf-8"))
    print(f"Sentencias Cypher a ejecutar: {len(statements)}")

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    try:
        driver.verify_connectivity()
    except Exception as exc:
        print(f"ERROR conectando a Neo4j: {exc}")
        sys.exit(1)

    with driver.session() as session:
        for idx, stmt in enumerate(statements, start=1):
            print(f"[{idx}/{len(statements)}] Ejecutando...", end=" ", flush=True)
            session.run(stmt).consume()
            print("OK")
    driver.close()

    print("\nCarga estructurada de rutinas completada.")
    print("Validacion:")
    print("MATCH (r:Routine) RETURN count(r) AS rutinas;")
    print("MATCH (p:Program)-[:CALLS]->(r:Routine) RETURN count(*) AS llamadas_programa_rutina;")


if __name__ == "__main__":
    main()
