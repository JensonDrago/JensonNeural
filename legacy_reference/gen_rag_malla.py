"""
Genera rag_malla.jsonl desde los CSV de mallas Control-M.

Uso:
  python D:\Jenson\HostIA\gen_rag_malla.py
  python D:\Jenson\HostIA\gen_rag_malla.py --csv-dir D:\Jenson\HostIA\malla_neo4j_csv_multi
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


DEFAULT_CSV_DIR = Path(r"D:\Jenson\HostIA\malla_neo4j_csv_multi")


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def clean(value: object) -> str:
    return str(value or "").strip()


def unique_sorted(values: list[str]) -> list[str]:
    return sorted({v for v in values if v})


def doc_id_from_object_id(object_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", object_id).strip("_")
    return f"MALLA_OBJ_{safe}"


def condition_label(row: dict) -> str:
    name = clean(row.get("condition_name"))
    odat = clean(row.get("odat"))
    return f"{name} ({odat})" if odat else name


def build_docs(csv_dir: Path) -> list[dict]:
    objects_rows = read_csv(csv_dir / "malla_object.csv")
    input_rows = read_csv(csv_dir / "malla_object_input_condition.csv")
    output_rows = read_csv(csv_dir / "malla_object_output_condition.csv")
    dependency_rows = read_csv(csv_dir / "malla_job_dependency.csv")

    objects = {clean(r.get("object_id")): r for r in objects_rows if clean(r.get("object_id"))}

    inputs_by_object: dict[str, list[str]] = defaultdict(list)
    outputs_by_object: dict[str, list[str]] = defaultdict(list)
    preds_by_object: dict[str, list[str]] = defaultdict(list)
    succs_by_object: dict[str, list[str]] = defaultdict(list)

    for row in input_rows:
        oid = clean(row.get("object_id"))
        if oid:
            inputs_by_object[oid].append(condition_label(row))

    for row in output_rows:
        oid = clean(row.get("object_id"))
        if oid:
            outputs_by_object[oid].append(condition_label(row))

    for row in dependency_rows:
        pred_oid = clean(row.get("predecessor_object_id"))
        succ_oid = clean(row.get("successor_object_id"))
        pred_name = clean(row.get("predecessor_memname"))
        succ_name = clean(row.get("successor_memname"))
        if pred_oid and succ_name:
            succs_by_object[pred_oid].append(succ_name)
        if succ_oid and pred_name:
            preds_by_object[succ_oid].append(pred_name)

    docs = []
    for oid, obj in sorted(objects.items()):
        memname = clean(obj.get("memname"))
        malla_id = clean(obj.get("malla_id"))
        typ = clean(obj.get("typ"))
        group = clean(obj.get("group"))
        table = clean(obj.get("table"))
        description = clean(obj.get("description"))
        source_file = clean(obj.get("source_file"))

        inputs = unique_sorted(inputs_by_object.get(oid, []))
        outputs = unique_sorted(outputs_by_object.get(oid, []))
        preds = unique_sorted(preds_by_object.get(oid, []))
        succs = unique_sorted(succs_by_object.get(oid, []))

        parts = [
            f"Objeto de malla {memname} de tipo {typ} en malla {malla_id}, grupo {group} y tabla {table}."
        ]
        if description:
            parts.append(f"Descripcion: {description}.")
        if inputs:
            parts.append(f"Condiciones de entrada: {', '.join(inputs)}.")
        if outputs:
            parts.append(f"Condiciones de salida: {', '.join(outputs)}.")
        if preds:
            parts.append(f"Depende de jobs/objetos: {', '.join(preds)}.")
        if succs:
            parts.append(f"Habilita jobs/objetos: {', '.join(succs)}.")

        docs.append(
            {
                "id": doc_id_from_object_id(oid),
                "text": " ".join(parts),
                "metadata": {
                    "type": "malla_object",
                    "object_id": oid,
                    "malla_id": malla_id,
                    "source_file": source_file,
                    "source_ref": source_file,
                    "memname": memname,
                    "object_type": typ,
                    "group": group,
                    "table": table,
                    "input_conditions": inputs,
                    "output_conditions": outputs,
                    "predecessors": preds,
                    "successors": succs,
                },
            }
        )

    return docs


def write_jsonl(docs: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as f:
        for doc in docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera rag_malla.jsonl desde CSV de mallas.")
    parser.add_argument("--csv-dir", default=str(DEFAULT_CSV_DIR), help="Carpeta con malla_object.csv y relaciones.")
    parser.add_argument("--output", default="", help="Ruta de salida. Por defecto: <csv-dir>\\rag_malla.jsonl")
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    output = Path(args.output) if args.output else csv_dir / "rag_malla.jsonl"
    docs = build_docs(csv_dir)
    write_jsonl(docs, output)

    print(f"Generado: {output}")
    print(f"Documentos malla_object: {len(docs)}")


if __name__ == "__main__":
    main()
