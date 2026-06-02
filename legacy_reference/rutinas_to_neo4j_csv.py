"""
Parser de rutinas COBOL a CSV Neo4j + RAG JSONL.

Entrada por defecto:
  D:\Jenson\HostIA\Rutinas_example

Salida por defecto:
  D:\Jenson\HostIA\rutinas_neo4j_csv

Uso:
  python D:\Jenson\HostIA\rutinas_to_neo4j_csv.py
  python D:\Jenson\HostIA\rutinas_to_neo4j_csv.py --input-dir D:\Jenson\HostIA\Rutinas_example --output-dir D:\Jenson\HostIA\rutinas_neo4j_csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_INPUT_DIR = Path(r"D:\Jenson\HostIA\Rutinas_example")
DEFAULT_OUTPUT_DIR = Path(r"D:\Jenson\HostIA\Rutinas_example\rutinas_neo4j_csv")
DEFAULT_PROGRAM_CALLS = Path(r"D:\Jenson\HostIA\SRC_example\neo4j_csv\program_calls_routine.csv")

SOURCE_EXTENSIONS = {".cbl", ".cob", ".cobol", ".cpy", ".txt"}
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1", "cp037", "cp1140")
SQL_OPS = ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "DECLARE", "OPEN", "FETCH", "CLOSE")
SQL_KEYWORDS = {
    "AS", "AT", "BY", "FOR", "FROM", "IN", "INTO", "JOIN", "OF", "ON", "ORDER",
    "SET", "TO", "UPDATE", "WHERE", "WITH", "VALUES",
}
SENSITIVE_TERMS = (
    "CLAVE", "PASSWORD", "PASS", "TOKEN", "SECRE", "CUENTA", "TARJ", "CARD",
    "PAN", "DNI", "RUC", "NIF", "DOC", "CLIENT", "EMAIL", "MAIL", "TELEF",
)


def read_text(path: Path) -> str:
    last_error = None
    for enc in ENCODINGS:
        try:
            return path.read_text(encoding=enc)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"No se pudo leer {path}: {last_error}")


def cobol_area(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.rstrip("\n\r")
        indicator = line[6:7] if len(line) > 6 else ""
        if indicator in ("*", "/", "D"):
            continue
        if len(line) >= 72:
            line = line[6:72]
        elif len(line) > 6:
            line = line[6:]
        line = line.rstrip()
        if line.strip():
            lines.append(line)
    return lines


def norm_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def csv_write(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def section_text(lines: list[str], start_pat: str, end_pats: tuple[str, ...]) -> str:
    active = False
    out = []
    start_re = re.compile(start_pat, re.I)
    end_res = [re.compile(p, re.I) for p in end_pats]
    for line in lines:
        if not active and start_re.search(line):
            active = True
        if active:
            if out and any(r.search(line) for r in end_res):
                break
            out.append(line)
    return "\n".join(out)


def extract_program_id(lines: list[str], fallback: str) -> str:
    text = "\n".join(lines)
    m = re.search(r"\bPROGRAM-ID\s*\.\s*([A-Z0-9#$@_-]+)", text, re.I)
    return (m.group(1) if m else fallback).upper().replace("-", "_")


def extract_author_date(lines: list[str]) -> tuple[str, str]:
    text = "\n".join(lines[:120])
    author = ""
    date_written = ""
    m = re.search(r"\bAUTHOR\s*\.\s*([^\n.]+)", text, re.I)
    if m:
        author = norm_space(m.group(1))
    m = re.search(r"\bDATE-WRITTEN\s*\.\s*([^\n.]+)", text, re.I)
    if m:
        date_written = norm_space(m.group(1))
    return author, date_written


def extract_copybooks(text: str) -> list[str]:
    return sorted({m.group(1).upper() for m in re.finditer(r"\bCOPY\s+([A-Z0-9#$@_-]+)", text, re.I)})


def extract_calls(text: str) -> list[dict]:
    rows = []
    for m in re.finditer(r"\bCALL\s+(?:['\"]([A-Z0-9#$@_-]+)['\"]|([A-Z0-9#$@_-]+))", text, re.I):
        literal = (m.group(1) or m.group(2) or "").upper()
        if not literal or literal in {"WS", "DFHEI1"}:
            continue
        rows.append({
            "called_routine_name": literal,
            "call_type": "STATIC" if m.group(1) else "DYNAMIC",
            "confidence": "0.99" if m.group(1) else "0.75",
        })
    return rows


def extract_exec_sql_blocks(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"EXEC\s+SQL(.*?)END-EXEC", text, re.I | re.S)]


def clean_table_name(value: str) -> str:
    value = re.sub(r"[,.;()]+$", "", value or "").strip().upper()
    return value.replace('"', "")


def extract_db2(sql_blocks: list[str]) -> list[dict]:
    table_ops = {}
    for block in sql_blocks:
        b = norm_space(block).upper()
        ops = {op for op in SQL_OPS if re.search(rf"\b{op}\b", b)}
        candidates = []
        candidates += re.findall(r"\bFROM\s+([A-Z0-9_.$#@]+)", b)
        candidates += re.findall(r"\bJOIN\s+([A-Z0-9_.$#@]+)", b)
        candidates += re.findall(r"\bUPDATE\s+([A-Z0-9_.$#@]+)", b)
        candidates += re.findall(r"\bINTO\s+([A-Z0-9_.$#@]+)", b) if "INSERT" in ops else []
        for table in candidates:
            table = clean_table_name(table)
            if not table or table.startswith(":") or table in SQL_KEYWORDS:
                continue
            table_ops.setdefault(table, set()).update(ops or {"SQL"})
    return [{"table": t, "operations": sorted(ops)} for t, ops in sorted(table_ops.items())]


def extract_variables(lines: list[str], routine_id: str, sql_blocks: list[str]) -> list[dict]:
    data_text = section_text(lines, r"\bDATA\s+DIVISION\b", (r"\bPROCEDURE\s+DIVISION\b",))
    linkage_text = section_text(lines, r"\bLINKAGE\s+SECTION\b", (r"\bPROCEDURE\s+DIVISION\b", r"\bWORKING-STORAGE\s+SECTION\b", r"\bFILE\s+SECTION\b"))
    assigned = {m.group(1).upper() for m in re.finditer(r"\b(?:MOVE\s+.+?\s+TO|SET|ADD\s+.+?\s+TO|SUBTRACT\s+.+?\s+FROM)\s+([A-Z0-9#$@_-]+)", "\n".join(lines), re.I)}
    sql_into = {m.group(1).upper().lstrip(":") for block in sql_blocks for m in re.finditer(r"[:]\s*([A-Z0-9#$@_-]+)", block, re.I)}
    output_names = assigned | sql_into

    rows = []
    seen = set()
    for m in re.finditer(r"^\s*(0[1-9]|[1-4][0-9])\s+([A-Z0-9#$@_-]+)(.*?)(?:\.|$)", data_text, re.I | re.M):
        level, name, tail = m.group(1), m.group(2).upper(), norm_space(m.group(3))
        if name in seen or name in {"FILLER"}:
            continue
        seen.add(name)
        source_type = "LINKAGE" if re.search(rf"\b{re.escape(name)}\b", linkage_text, re.I) else "WORKING_STORAGE"
        pic = ""
        pic_m = re.search(r"\bPIC(?:TURE)?\s+([A-Z0-9()VXS9.,+-]+)", tail, re.I)
        if pic_m:
            pic = pic_m.group(1).upper()
        direction = "INTERNAL"
        if source_type == "LINKAGE":
            direction = "OUTPUT" if name in output_names else "INPUT"
            if name in output_names and re.search(rf"\b{re.escape(name)}\b", "\n".join(lines), re.I):
                direction = "INOUT"
        rows.append({
            "variable_id": f"{routine_id}:{name}",
            "routine_id": routine_id,
            "name": name,
            "normalized_name": name.replace("-", "_"),
            "level": level,
            "direction": direction,
            "data_type": "PIC" if pic else "",
            "pic_type": pic,
            "source_type": source_type,
        })
    return rows


def score_level(score: int) -> str:
    if score >= 75:
        return "ALTO"
    if score >= 45:
        return "MEDIO"
    return "BAJO"


def calculate_axes(loc: int, text: str, variables: list[dict], db2: list[dict], calls: list[dict], invoked_by: int, copy_count: int) -> dict:
    upper = text.upper()
    decisions = len(re.findall(r"\b(IF|EVALUATE|WHEN|PERFORM\s+UNTIL|PERFORM\s+VARYING)\b", upper))
    loops = len(re.findall(r"\b(PERFORM\s+UNTIL|PERFORM\s+VARYING|FETCH)\b", upper))
    gotos = len(re.findall(r"\bGO\s+TO\b", upper))
    dynamic_calls = sum(1 for c in calls if c["call_type"] == "DYNAMIC")
    sensitive_vars = sorted({
        v["name"] for v in variables
        if any(term in v["name"] for term in SENSITIVE_TERMS)
    })
    sensitive_hits = len(sensitive_vars)
    write_ops = sum(1 for d in db2 if {"UPDATE", "DELETE", "INSERT"} & set(d["operations"]))

    complexity = min(100, decisions * 3 + loops * 5 + len(db2) * 4 + loc // 120)
    debt = min(100, gotos * 8 + dynamic_calls * 10 + max(0, loc - 800) // 25 + copy_count * 2)
    business = min(100, invoked_by * 12 + len(db2) * 10 + len(calls) * 3 + len([v for v in variables if v["direction"] in ("INPUT", "OUTPUT", "INOUT")]))
    security = min(100, sensitive_hits * 12 + write_ops * 8 + ("PASSWORD" in upper or "CLAVE" in upper) * 20)
    proc_time = min(100, loops * 10 + len(db2) * 8 + loc // 150)

    return {
        "technical_debt_score": debt,
        "technical_debt_level": score_level(debt),
        "technical_debt_rationale": f"GO TO={gotos}, llamadas dinamicas={dynamic_calls}, copybooks={copy_count}, LOC={loc}",
        "complexity_score": complexity,
        "complexity_level": score_level(complexity),
        "complexity_rationale": f"Decisiones={decisions}, loops/cursors={loops}, tablas DB2={len(db2)}, LOC={loc}",
        "business_value_score": business,
        "business_value_level": score_level(business),
        "business_value_rationale": f"Invocada por {invoked_by} programas, tablas DB2={len(db2)}, variables interface={len(variables)}",
        "security_score": security,
        "security_level": score_level(security),
        "security_rationale": (
            f"Variables sensibles={sensitive_hits}"
            + (f" ({', '.join(sensitive_vars[:30])})" if sensitive_vars else "")
            + f", operaciones escritura DB2={write_ops}"
        ),
        "processing_time_score": proc_time,
        "processing_time_level": score_level(proc_time),
        "processing_time_rationale": f"Loops/cursors={loops}, accesos DB2={len(db2)}, LOC={loc}",
    }


def load_program_calls(path: Path) -> tuple[dict[str, int], dict[str, list[str]], list[dict]]:
    counts = Counter()
    programs_by_call = defaultdict(list)
    rows = []
    if not path.exists():
        return {}, {}, []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            call_name = (row.get("call_literal_name") or "").upper()
            if call_name:
                counts[call_name] += 1
                if row.get("program_id"):
                    programs_by_call[call_name].append(row["program_id"])
                rows.append(row)
    return dict(counts), {k: sorted(set(v)) for k, v in programs_by_call.items()}, rows


def parse_sources(input_dir: Path, program_calls_path: Path) -> dict[str, list[dict]]:
    invoked_counts, programs_by_call, program_call_rows = load_program_calls(program_calls_path)
    files = sorted(p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in SOURCE_EXTENSIONS)

    routines = []
    source_artifacts = []
    routine_has_source = []
    variables = []
    routine_has_variable = []
    db2tables = {}
    routine_accesses_db2 = []
    routine_calls = []
    axes_rows = []
    rag_docs = []

    routine_names = set()
    parsed_calls_by_routine = defaultdict(list)

    parsed = []
    for path in files:
        raw = read_text(path)
        lines = cobol_area(raw)
        text = "\n".join(lines)
        routine_id = extract_program_id(lines, path.stem).upper()
        routine_names.add(routine_id)
        author, date_written = extract_author_date(lines)
        copybooks = extract_copybooks(text)
        calls = extract_calls(text)
        sql_blocks = extract_exec_sql_blocks(text)
        db2 = extract_db2(sql_blocks)
        vars_ = extract_variables(lines, routine_id, sql_blocks)
        loc = len([ln for ln in lines if ln.strip()])
        invoked_by = invoked_counts.get(routine_id, 0)
        axes = calculate_axes(loc, text, vars_, db2, calls, invoked_by, len(copybooks))
        parsed.append((path, routine_id, author, date_written, copybooks, calls, db2, vars_, loc, invoked_by, axes))
        parsed_calls_by_routine[routine_id].extend(calls)

    for path, routine_id, author, date_written, copybooks, calls, db2, vars_, loc, invoked_by, axes in parsed:
        source_ref = f"repo://rutinas/{path.name}"
        routines.append({
            "routine_id": routine_id,
            "name": routine_id,
            "language": "COBOL",
            "routine_type": "SUBPROGRAM",
            "call_style": "STATIC_CAPABLE",
            "loc": loc,
            "cyclomatic_score": axes["complexity_score"],
            "copybook_count": len(copybooks),
            "debt_score": axes["technical_debt_score"],
            "criticality": axes["business_value_level"],
            "business_domain": "",
            "author": author,
            "date_written": date_written,
            "source_ref": source_ref,
        })
        source_artifacts.append({
            "artifact_id": path.stem.upper(),
            "artifact_type": "ROUTINE",
            "uri": str(path),
            "version": "v1",
        })
        routine_has_source.append({"routine_id": routine_id, "artifact_id": path.stem.upper()})
        for var in vars_:
            variables.append(var)
            routine_has_variable.append({"routine_id": routine_id, "variable_id": var["variable_id"]})
        for item in db2:
            table_id = item["table"]
            db2tables[table_id] = {"db2table_id": table_id, "schema": table_id.split(".")[0] if "." in table_id else "", "name": table_id.split(".")[-1]}
            for op in item["operations"]:
                routine_accesses_db2.append({
                    "routine_id": routine_id,
                    "db2table_id": table_id,
                    "operation": op,
                    "confidence": "0.90",
                })
        for call in calls:
            target = call["called_routine_name"]
            if target in routine_names:
                routine_calls.append({
                    "caller_routine_id": routine_id,
                    "called_routine_id": target,
                    "call_type": call["call_type"],
                    "call_literal_name": target,
                    "call_count": "1",
                    "confidence": call["confidence"],
                })
        axes_rows.append({"routine_id": routine_id, **axes})

        input_vars = sorted(v["name"] for v in vars_ if v["direction"] in ("INPUT", "INOUT"))
        output_vars = sorted(v["name"] for v in vars_ if v["direction"] in ("OUTPUT", "INOUT"))
        db2_text = [f"{d['table']} ({', '.join(d['operations'])})" for d in db2]
        called = sorted({c["called_routine_name"] for c in calls})
        invokers = programs_by_call.get(routine_id, [])
        rag_text = (
            f"Rutina COBOL {routine_id}. LOC={loc}. Invocada por {invoked_by} programas. "
            f"Programas que la invocan: {', '.join(invokers[:40]) if invokers else 'sin programas invocadores detectados'}. "
            f"Variables de entrada: {', '.join(input_vars[:30]) if input_vars else 'sin variables de entrada detectadas'}. "
            f"Variables de salida: {', '.join(output_vars[:30]) if output_vars else 'sin variables de salida detectadas'}. "
            f"Tablas DB2: {', '.join(db2_text) if db2_text else 'sin DB2 detectado'}. "
            f"Rutinas invocadas: {', '.join(called) if called else 'sin llamadas a rutinas detectadas'}. "
            f"Ejes: deuda tecnica {axes['technical_debt_score']}/100 {axes['technical_debt_level']}; "
            f"complejidad {axes['complexity_score']}/100 {axes['complexity_level']}; "
            f"valor negocio {axes['business_value_score']}/100 {axes['business_value_level']}; "
            f"riesgo seguridad {axes['security_score']}/100 {axes['security_level']}; "
            f"tiempo proceso {axes['processing_time_score']}/100 {axes['processing_time_level']}. "
            f"Justificacion seguridad: {axes['security_rationale']}."
        )
        rag_docs.append({
            "id": f"ROUTINE_{routine_id}",
            "text": rag_text,
            "metadata": {
                "type": "routine",
                "routine_id": routine_id,
                "source_ref": source_ref,
                "input_variables": input_vars,
                "output_variables": output_vars,
                "invoked_by_programs": invokers,
                "db2_tables": db2_text,
                "called_routines": called,
                "axes": axes,
            },
        })

    resolved_program_calls = []
    routine_names = {r["routine_id"] for r in routines}
    for row in program_call_rows:
        call_name = (row.get("call_literal_name") or "").upper()
        if call_name in routine_names:
            resolved_program_calls.append({
                "program_id": row.get("program_id", ""),
                "routine_id": call_name,
                "call_type": row.get("call_type", ""),
                "call_literal_name": call_name,
                "call_count": row.get("call_count", "1"),
                "confidence": row.get("confidence", "0.95"),
                "source_ref": row.get("source_ref", ""),
            })

    return {
        "routine.csv": routines,
        "source_artifact.csv": source_artifacts,
        "routine_has_source.csv": routine_has_source,
        "routine_variable.csv": variables,
        "routine_has_variable.csv": routine_has_variable,
        "db2table.csv": list(db2tables.values()),
        "routine_accesses_db2.csv": routine_accesses_db2,
        "routine_calls_routine.csv": routine_calls,
        "routine_axes.csv": axes_rows,
        "program_calls_routine_resolved.csv": resolved_program_calls,
        "rag_rutinas.jsonl": rag_docs,
    }


def write_loader(output_dir: Path) -> None:
    csv_base = str(output_dir).replace("\\", "/")
    loader = f"""
CREATE CONSTRAINT routine_id IF NOT EXISTS FOR (r:Routine) REQUIRE r.routine_id IS UNIQUE;
CREATE CONSTRAINT routine_variable_id IF NOT EXISTS FOR (v:RoutineVariable) REQUIRE v.variable_id IS UNIQUE;
CREATE CONSTRAINT db2table_id IF NOT EXISTS FOR (t:DB2Table) REQUIRE t.db2table_id IS UNIQUE;
CREATE CONSTRAINT source_artifact_id IF NOT EXISTS FOR (a:SourceArtifact) REQUIRE a.artifact_id IS UNIQUE;

LOAD CSV WITH HEADERS FROM 'file:///{csv_base}/routine.csv' AS row
MERGE (r:Routine {{routine_id: row.routine_id}})
SET r.name = row.name,
    r.language = row.language,
    r.routine_type = row.routine_type,
    r.call_style = row.call_style,
    r.loc = toIntegerOrNull(row.loc),
    r.cyclomatic_score = toIntegerOrNull(row.cyclomatic_score),
    r.copybook_count = toIntegerOrNull(row.copybook_count),
    r.debt_score = toIntegerOrNull(row.debt_score),
    r.criticality = row.criticality,
    r.business_domain = row.business_domain,
    r.author = row.author,
    r.date_written = row.date_written,
    r.source_ref = row.source_ref;

LOAD CSV WITH HEADERS FROM 'file:///{csv_base}/routine_axes.csv' AS row
MATCH (r:Routine {{routine_id: row.routine_id}})
SET r.technical_debt_score = toIntegerOrNull(row.technical_debt_score),
    r.technical_debt_level = row.technical_debt_level,
    r.technical_debt_rationale = row.technical_debt_rationale,
    r.complexity_score = toIntegerOrNull(row.complexity_score),
    r.complexity_level = row.complexity_level,
    r.complexity_rationale = row.complexity_rationale,
    r.business_value_score = toIntegerOrNull(row.business_value_score),
    r.business_value_level = row.business_value_level,
    r.business_value_rationale = row.business_value_rationale,
    r.security_score = toIntegerOrNull(row.security_score),
    r.security_level = row.security_level,
    r.security_rationale = row.security_rationale,
    r.processing_time_score = toIntegerOrNull(row.processing_time_score),
    r.processing_time_level = row.processing_time_level,
    r.processing_time_rationale = row.processing_time_rationale;

LOAD CSV WITH HEADERS FROM 'file:///{csv_base}/source_artifact.csv' AS row
MERGE (a:SourceArtifact {{artifact_id: row.artifact_id}})
SET a.artifact_type = row.artifact_type,
    a.uri = row.uri,
    a.version = row.version;

LOAD CSV WITH HEADERS FROM 'file:///{csv_base}/routine_has_source.csv' AS row
MATCH (r:Routine {{routine_id: row.routine_id}})
MATCH (a:SourceArtifact {{artifact_id: row.artifact_id}})
MERGE (r)-[:HAS_SOURCE]->(a);

LOAD CSV WITH HEADERS FROM 'file:///{csv_base}/routine_variable.csv' AS row
MERGE (v:RoutineVariable {{variable_id: row.variable_id}})
SET v.routine_id = row.routine_id,
    v.name = row.name,
    v.normalized_name = row.normalized_name,
    v.level = row.level,
    v.direction = row.direction,
    v.data_type = row.data_type,
    v.pic_type = row.pic_type,
    v.source_type = row.source_type;

LOAD CSV WITH HEADERS FROM 'file:///{csv_base}/routine_has_variable.csv' AS row
MATCH (r:Routine {{routine_id: row.routine_id}})
MATCH (v:RoutineVariable {{variable_id: row.variable_id}})
MERGE (r)-[:HAS_VARIABLE]->(v);

LOAD CSV WITH HEADERS FROM 'file:///{csv_base}/db2table.csv' AS row
MERGE (t:DB2Table {{db2table_id: row.db2table_id}})
SET t.schema = row.schema,
    t.name = row.name;

LOAD CSV WITH HEADERS FROM 'file:///{csv_base}/routine_accesses_db2.csv' AS row
MATCH (r:Routine {{routine_id: row.routine_id}})
MATCH (t:DB2Table {{db2table_id: row.db2table_id}})
MERGE (r)-[rel:ACCESSES_DB2]->(t)
SET rel.operation = row.operation,
    rel.confidence = toFloatOrNull(row.confidence);

LOAD CSV WITH HEADERS FROM 'file:///{csv_base}/routine_calls_routine.csv' AS row
MATCH (caller:Routine {{routine_id: row.caller_routine_id}})
MATCH (called:Routine {{routine_id: row.called_routine_id}})
MERGE (caller)-[rel:CALLS]->(called)
SET rel.call_type = row.call_type,
    rel.call_literal_name = row.call_literal_name,
    rel.call_count = toIntegerOrNull(row.call_count),
    rel.confidence = toFloatOrNull(row.confidence);

LOAD CSV WITH HEADERS FROM 'file:///{csv_base}/program_calls_routine_resolved.csv' AS row
MATCH (p:Program {{program_id: row.program_id}})
MATCH (r:Routine {{routine_id: row.routine_id}})
MERGE (p)-[rel:CALLS]->(r)
SET rel.call_type = row.call_type,
    rel.call_literal_name = row.call_literal_name,
    rel.call_count = toIntegerOrNull(row.call_count),
    rel.confidence = toFloatOrNull(row.confidence),
    rel.source_ref = row.source_ref;
"""
    (output_dir / "load_rutinas.cypher").write_text(loader.strip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parser de rutinas COBOL para Neo4j GraphRAG.")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--program-calls", default=str(DEFAULT_PROGRAM_CALLS))
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data = parse_sources(input_dir, Path(args.program_calls))

    headers = {
        "routine.csv": ["routine_id", "name", "language", "routine_type", "call_style", "loc", "cyclomatic_score", "copybook_count", "debt_score", "criticality", "business_domain", "author", "date_written", "source_ref"],
        "source_artifact.csv": ["artifact_id", "artifact_type", "uri", "version"],
        "routine_has_source.csv": ["routine_id", "artifact_id"],
        "routine_variable.csv": ["variable_id", "routine_id", "name", "normalized_name", "level", "direction", "data_type", "pic_type", "source_type"],
        "routine_has_variable.csv": ["routine_id", "variable_id"],
        "db2table.csv": ["db2table_id", "schema", "name"],
        "routine_accesses_db2.csv": ["routine_id", "db2table_id", "operation", "confidence"],
        "routine_calls_routine.csv": ["caller_routine_id", "called_routine_id", "call_type", "call_literal_name", "call_count", "confidence"],
        "program_calls_routine_resolved.csv": ["program_id", "routine_id", "call_type", "call_literal_name", "call_count", "confidence", "source_ref"],
        "routine_axes.csv": ["routine_id", "technical_debt_score", "technical_debt_level", "technical_debt_rationale", "complexity_score", "complexity_level", "complexity_rationale", "business_value_score", "business_value_level", "business_value_rationale", "security_score", "security_level", "security_rationale", "processing_time_score", "processing_time_level", "processing_time_rationale"],
    }
    for filename, fieldnames in headers.items():
        csv_write(output_dir / filename, fieldnames, data[filename])

    with (output_dir / "rag_rutinas.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for doc in data["rag_rutinas.jsonl"]:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    write_loader(output_dir)

    print(f"Salida: {output_dir}")
    print(f"Rutinas: {len(data['routine.csv'])}")
    print(f"Variables: {len(data['routine_variable.csv'])}")
    print(f"Accesos DB2: {len(data['routine_accesses_db2.csv'])}")
    print(f"Calls rutina->rutina: {len(data['routine_calls_routine.csv'])}")
    print(f"Calls programa->rutina resueltos: {len(data['program_calls_routine_resolved.csv'])}")
    print(f"RAG docs: {len(data['rag_rutinas.jsonl'])}")


if __name__ == "__main__":
    main()
