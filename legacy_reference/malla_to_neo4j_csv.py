"""
Malla Control-M -> Neo4j CSV / GraphRAG
======================================

Parsea uno o varios reportes de malla Control-M en formato de ancho fijo, como
Malla_MPRILION.TXT, y genera CSV consolidados para Neo4j:

  - malla_object.csv
  - malla_condition.csv
  - malla_object_input_condition.csv
  - malla_object_output_condition.csv
  - malla_job_dependency.csv
  - rag_malla.jsonl
  - load_malla.cypher

Uso con un archivo:
  python malla_to_neo4j_csv.py D:\Jenson\HostIA\Malla_MPRILION.TXT

Uso con una carpeta:
  python malla_to_neo4j_csv.py D:\Jenson\HostIA\mallas --pattern "*.TXT"

Nota sobre Op:
  Por defecto se interpreta Op vacio como condicion de entrada y Op '+' como
  condicion de salida, que es el patron usual de Control-M y coincide con el
  ejemplo MPJP180E / UGJP3200-OK del archivo. Si tu reporte viene invertido,
  usa --plus-is-input.
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


COLUMN_SPECS = [
    ("ps", 0, 3),
    ("ac", 3, 6),
    ("memname", 6, 23),
    ("ds_jobname", 23, 34),
    ("condition_name", 34, 51),
    ("odat", 51, 56),
    ("op", 56, 59),
    ("owner", 59, 65),
    ("typ", 65, 69),
    ("group", 69, 78),
    ("description", 78, 129),
    ("control_m", 129, 139),
    ("schedule_library", 139, 162),
    ("application", 162, 174),
    ("table", 174, 183),
    ("t", 183, 185),
    ("unique", 185, None),
]

HEADER_ALIASES = {
    "ps": "Ps",
    "ac": "Ac",
    "memname": "MEMNAME/Filename",
    "ds_jobname": "DS-Jobname",
    "condition_name": "Condition-Name",
    "odat": "ODAT",
    "op": "Op",
    "owner": "Owner",
    "typ": "Typ",
    "group": "Group",
    "description": "Description",
    "control_m": "Control-M",
    "schedule_library": "Schedule-Library",
    "application": "Application",
    "table": "Table",
    "t": "T",
    "unique": "Unique",
}


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def clean(value: str) -> str:
    return (value or "").strip()


def condition_id(malla_id: str, name: str, odat: str) -> str:
    return f"{clean(malla_id)}|{clean(name)}|{clean(odat)}"


def object_id(malla_id: str, row: dict) -> str:
    unique = clean(row.get("unique"))
    memname = clean(row.get("memname"))
    table = clean(row.get("table"))
    if unique:
        return f"{malla_id}:{table}:{memname}:{unique}"
    return f"{malla_id}:{table}:{memname}"


def specs_from_header(header: str) -> list:
    positions = []
    for name, label in HEADER_ALIASES.items():
        if name == "t":
            table_pos = header.find("Table")
            unique_pos = header.find("Unique")
            pos = -1
            if table_pos >= 0 and unique_pos > table_pos:
                match = re.search(r"\bT\b", header[table_pos + len("Table"):unique_pos])
                if match:
                    pos = table_pos + len("Table") + match.start()
        else:
            pos = header.find(label)
        if pos >= 0:
            positions.append((pos, name))
    if len(positions) < 10:
        return COLUMN_SPECS
    positions.sort()
    return [
        (name, start, positions[i + 1][0] if i + 1 < len(positions) else None)
        for i, (start, name) in enumerate(positions)
    ]


def parse_line(line: str, specs: list) -> dict:
    row = {}
    for name, start, end in specs:
        row[name] = clean(line[start:end])
    return row


def is_data_row(row: dict) -> bool:
    if not row.get("memname") or row["memname"].upper() == "MEMNAME/FILENAME":
        return False
    if not row.get("condition_name"):
        return False
    return True


def condition_direction(op: str, plus_is_input: bool) -> str:
    op = clean(op)
    if plus_is_input:
        return "INPUT" if op == "+" else "OUTPUT"
    return "OUTPUT" if op == "+" else "INPUT"


def write_csv(path: Path, fieldnames: list, rows: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def discover_input_files(input_path: Path, pattern: str) -> list:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(p for p in input_path.glob(pattern) if p.is_file())
    raise FileNotFoundError(f"Ruta no encontrada: {input_path}")


def build_rows(input_paths: list, plus_is_input: bool):
    objects = {}
    conditions = {}
    input_rels = set()
    output_rels = set()
    raw_rows = []

    for input_path in input_paths:
        lines = read_text(input_path).splitlines()
        specs = COLUMN_SPECS
        for header_line in lines[:10]:
            if "MEMNAME/Filename" in header_line and "Condition-Name" in header_line:
                specs = specs_from_header(header_line)
                break
        for line_no, line in enumerate(lines, start=1):
            if line_no == 1 and "MEMNAME/Filename" in line:
                continue
            if not line.strip():
                continue
            row = parse_line(line, specs)
            if not is_data_row(row):
                continue

            malla_id = clean(row.get("table")) or input_path.stem
            oid = object_id(malla_id, row)
            cid = condition_id(malla_id, row["condition_name"], row["odat"])
            direction = condition_direction(row["op"], plus_is_input)

            objects[oid] = {
                "object_id": oid,
                "malla_id": malla_id,
                "source_file": input_path.name,
                "memname": row["memname"],
                "ds_jobname": row["ds_jobname"],
                "typ": row["typ"],
                "group": row["group"],
                "description": row["description"],
                "owner": row["owner"],
                "control_m": row["control_m"],
                "schedule_library": row["schedule_library"],
                "application": row["application"],
                "table": row["table"],
                "t": row["t"],
                "unique": row["unique"],
                "ps": row["ps"],
                "ac": row["ac"],
            }
            conditions[cid] = {
                "condition_id": cid,
                "malla_id": malla_id,
                "source_file": input_path.name,
                "name": row["condition_name"],
                "odat": row["odat"],
            }

            rel = (oid, cid, row["condition_name"], row["odat"], row["op"], malla_id)
            if direction == "INPUT":
                input_rels.add(rel)
            else:
                output_rels.add(rel)

            raw_rows.append((row, oid, cid, direction, malla_id))

    producers_by_condition = defaultdict(set)
    consumers_by_condition = defaultdict(set)
    for oid, cid, _name, _odat, _op, _malla_id in output_rels:
        producers_by_condition[cid].add(oid)
    for oid, cid, _name, _odat, _op, _malla_id in input_rels:
        consumers_by_condition[cid].add(oid)

    dependencies = set()
    for cid, consumers in consumers_by_condition.items():
        for producer in producers_by_condition.get(cid, set()):
            for consumer in consumers:
                if producer != consumer:
                    dependencies.add((producer, consumer, cid))

    return objects, conditions, input_rels, output_rels, dependencies, raw_rows


def build_rag(objects: dict, input_rels: set, output_rels: set, dependencies: set, out_path: Path):
    inputs_by_object = defaultdict(list)
    outputs_by_object = defaultdict(list)
    pred_by_object = defaultdict(list)
    succ_by_object = defaultdict(list)

    for oid, _cid, name, odat, _op, _malla_id in input_rels:
        inputs_by_object[oid].append(f"{name} ({odat})")
    for oid, _cid, name, odat, _op, _malla_id in output_rels:
        outputs_by_object[oid].append(f"{name} ({odat})")
    for pred, succ, cid in dependencies:
        pred_by_object[succ].append(objects[pred]["memname"])
        succ_by_object[pred].append(objects[succ]["memname"])

    with out_path.open("w", encoding="utf-8") as f:
        for oid, obj in sorted(objects.items()):
            inputs = sorted(set(inputs_by_object.get(oid, [])))
            outputs = sorted(set(outputs_by_object.get(oid, [])))
            preds = sorted(set(pred_by_object.get(oid, [])))
            succs = sorted(set(succ_by_object.get(oid, [])))
            text = (
                f"Objeto de malla {obj['memname']} de tipo {obj['typ']} "
                f"en malla {obj['malla_id']}, grupo {obj['group']} y tabla {obj['table']}. "
                f"Descripcion: {obj['description']}."
            )
            if inputs:
                text += f" Condiciones de entrada: {', '.join(inputs)}."
            if outputs:
                text += f" Condiciones de salida: {', '.join(outputs)}."
            if preds:
                text += f" Depende de jobs/objetos: {', '.join(preds)}."
            if succs:
                text += f" Habilita jobs/objetos: {', '.join(succs)}."

            doc = {
                "id": "MALLA_OBJ_" + oid.replace(":", "_").replace("|", "_"),
                "text": text,
                "metadata": {
                    "type": "malla_object",
                    "object_id": oid,
                    "malla_id": obj["malla_id"],
                    "source_file": obj["source_file"],
                    "memname": obj["memname"],
                    "object_type": obj["typ"],
                    "group": obj["group"],
                    "table": obj["table"],
                    "input_conditions": inputs,
                    "output_conditions": outputs,
                    "predecessors": preds,
                    "successors": succs,
                },
            }
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")


def write_loader(out_dir: Path):
    loader = r"""
CREATE CONSTRAINT con_malla_object_id IF NOT EXISTS
FOR (n:MallaObject) REQUIRE n.object_id IS UNIQUE;

CREATE CONSTRAINT con_malla_condition_id IF NOT EXISTS
FOR (n:MallaCondition) REQUIRE n.condition_id IS UNIQUE;

LOAD CSV WITH HEADERS FROM 'file:///malla_object.csv' AS row
MERGE (m:MallaObject {object_id: row.object_id})
SET m.malla_id = row.malla_id,
    m.source_file = row.source_file,
    m.memname = row.memname,
    m.ds_jobname = row.ds_jobname,
    m.typ = row.typ,
    m.group = row.group,
    m.description = row.description,
    m.owner = row.owner,
    m.control_m = row.control_m,
    m.schedule_library = row.schedule_library,
    m.application = row.application,
    m.table = row.table,
    m.t = row.t,
    m.unique = row.unique,
    m.ps = row.ps,
    m.ac = row.ac;

LOAD CSV WITH HEADERS FROM 'file:///malla_condition.csv' AS row
MERGE (c:MallaCondition {condition_id: row.condition_id})
SET c.malla_id = row.malla_id,
    c.source_file = row.source_file,
    c.name = row.name,
    c.odat = row.odat;

LOAD CSV WITH HEADERS FROM 'file:///malla_object_input_condition.csv' AS row
MATCH (m:MallaObject {object_id: row.object_id})
MATCH (c:MallaCondition {condition_id: row.condition_id})
MERGE (m)-[:REQUIRES_CONDITION {odat: row.odat, malla_id: row.malla_id}]->(c);

LOAD CSV WITH HEADERS FROM 'file:///malla_object_output_condition.csv' AS row
MATCH (m:MallaObject {object_id: row.object_id})
MATCH (c:MallaCondition {condition_id: row.condition_id})
MERGE (m)-[:PRODUCES_CONDITION {odat: row.odat, malla_id: row.malla_id}]->(c);

LOAD CSV WITH HEADERS FROM 'file:///malla_job_dependency.csv' AS row
MATCH (p:MallaObject {object_id: row.predecessor_object_id})
MATCH (s:MallaObject {object_id: row.successor_object_id})
MATCH (c:MallaCondition {condition_id: row.condition_id})
MERGE (p)-[:PRECEDES {condition_name: row.condition_name, odat: row.odat, malla_id: row.malla_id}]->(s)
MERGE (s)-[:DEPENDS_ON {condition_name: row.condition_name, odat: row.odat, malla_id: row.malla_id}]->(p);

// Vinculo opcional con nodos Job ya cargados por el parser JCL.
MATCH (m:MallaObject)
MATCH (j:Job {job_id: m.memname})
MERGE (m)-[:SCHEDULES_JOB]->(j);
"""
    (out_dir / "load_malla.cypher").write_text(loader.strip() + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Parsea mallas Control-M a CSV Neo4j.")
    parser.add_argument("input", help="Archivo de malla .TXT o carpeta con mallas")
    parser.add_argument(
        "output",
        nargs="?",
        help="Carpeta de salida. Default: <carpeta_base>\\malla_neo4j_csv",
    )
    parser.add_argument(
        "--output-dir",
        help="Carpeta de salida alternativa al argumento posicional output.",
    )
    parser.add_argument(
        "--pattern",
        default="*.TXT",
        help="Patron para buscar mallas cuando input es carpeta. Default: *.TXT",
    )
    parser.add_argument(
        "--plus-is-input",
        action="store_true",
        help="Interpreta Op '+' como entrada y Op vacio como salida.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    input_files = discover_input_files(input_path, args.pattern)
    if not input_files:
        raise SystemExit(f"No se encontraron archivos con patron {args.pattern} en {input_path}")

    default_base = input_path if input_path.is_dir() else input_path.parent
    out_dir = Path(args.output_dir or args.output) if (args.output_dir or args.output) else default_base / "malla_neo4j_csv"
    out_dir.mkdir(parents=True, exist_ok=True)

    objects, conditions, input_rels, output_rels, dependencies, _raw_rows = build_rows(
        input_files,
        plus_is_input=args.plus_is_input,
    )

    write_csv(out_dir / "malla_object.csv", [
        "object_id", "malla_id", "source_file", "memname", "ds_jobname", "typ", "group", "description",
        "owner", "control_m", "schedule_library", "application", "table",
        "t", "unique", "ps", "ac",
    ], sorted(objects.values(), key=lambda r: r["object_id"]))

    write_csv(out_dir / "malla_condition.csv", [
        "condition_id", "malla_id", "source_file", "name", "odat",
    ], sorted(conditions.values(), key=lambda r: r["condition_id"]))

    rel_fields = ["object_id", "condition_id", "condition_name", "odat", "op", "malla_id"]
    write_csv(out_dir / "malla_object_input_condition.csv", rel_fields, [
        {
            "object_id": oid,
            "condition_id": cid,
            "condition_name": name,
            "odat": odat,
            "op": op,
            "malla_id": malla_id,
        }
        for oid, cid, name, odat, op, malla_id in sorted(input_rels)
    ])
    write_csv(out_dir / "malla_object_output_condition.csv", rel_fields, [
        {
            "object_id": oid,
            "condition_id": cid,
            "condition_name": name,
            "odat": odat,
            "op": op,
            "malla_id": malla_id,
        }
        for oid, cid, name, odat, op, malla_id in sorted(output_rels)
    ])

    dep_rows = []
    for pred, succ, cid in sorted(dependencies):
        cond = conditions[cid]
        dep_rows.append({
            "predecessor_object_id": pred,
            "predecessor_memname": objects[pred]["memname"],
            "successor_object_id": succ,
            "successor_memname": objects[succ]["memname"],
            "condition_id": cid,
            "condition_name": cond["name"],
            "odat": cond["odat"],
            "malla_id": cond["malla_id"],
        })
    write_csv(out_dir / "malla_job_dependency.csv", [
        "predecessor_object_id", "predecessor_memname",
        "successor_object_id", "successor_memname",
        "condition_id", "condition_name", "odat", "malla_id",
    ], dep_rows)

    build_rag(objects, input_rels, output_rels, dependencies, out_dir / "rag_malla.jsonl")
    write_loader(out_dir)

    print("=== MALLA -> NEO4J CSV ===")
    print(f"Ruta entrada    : {input_path}")
    print(f"Archivos malla  : {len(input_files)}")
    for path in input_files[:10]:
        print(f"  - {path}")
    if len(input_files) > 10:
        print(f"  ... y {len(input_files) - 10} mas")
    print(f"Carpeta salida  : {out_dir}")
    print(f"Objetos malla   : {len(objects)}")
    print(f"Condiciones     : {len(conditions)}")
    print(f"Rel. entrada    : {len(input_rels)}")
    print(f"Rel. salida     : {len(output_rels)}")
    print(f"Dependencias    : {len(dep_rows)}")
    print("\nArchivos generados:")
    for name in (
        "malla_object.csv",
        "malla_condition.csv",
        "malla_object_input_condition.csv",
        "malla_object_output_condition.csv",
        "malla_job_dependency.csv",
        "rag_malla.jsonl",
        "load_malla.cypher",
    ):
        print(f"  - {out_dir / name}")


if __name__ == "__main__":
    main()
