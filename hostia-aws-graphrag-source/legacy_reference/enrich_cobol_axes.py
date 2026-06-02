"""
enrich_cobol_axes.py  —  Enriquecimiento de 5 Ejes de Análisis para Programas COBOL
=======================================================================================
Lee los archivos ya generados por el parser COBOL:
  · rag_cobol.jsonl   — documentos RAG existentes
  · program.csv       — atributos de cada programa

Calcula 5 ejes de análisis por programa y genera:
  · rag_cobol_enriched.jsonl  — JSONL enriquecido con los 5 ejes en metadata y texto
  · program_axes.csv          — CSV con los 5 ejes por programa (para carga en Neo4j)

EJES:
  1. Deuda Técnica    (technical_debt_score  0-100)
  2. Complejidad      (complexity_score      0-100)
  3. Valor de Negocio (business_value_score  0-100)
  4. Riesgo Seguridad (security_risk_score   0-100)
  5. Tiempo de Proc.  (processing_time_sec   ficticio/simulado)

Uso:
  python enrich_cobol_axes.py
  python enrich_cobol_axes.py --input-dir neo4j_csv --output-dir neo4j_csv
"""
import json
import csv
import os
import sys
import re
import hashlib
import argparse
from pathlib import Path

# ── Configuración de rutas ───────────────────────────────────────────────────
DEFAULT_DIR = Path(__file__).parent / "neo4j_csv"

# ── Palabras clave para inferencia de dominio de negocio ────────────────────
CRITICAL_KEYWORDS = {
    'keywords': ['PAGO','PAG','CRED','DEBI','LIQD','CIERRE','SALDO','COBR',
                 'FACT','EMIS','TRANS','REMIT','TARJ','HIPOT','PRESTA'],
    'db2_critical': ['OPERA','CUENTA','CONTRA','SALDO','MOVIM','TRANS','CLIEN'],
    'security_sensitive': ['CLAVE','PASS','TOKEN','SECU','CRYPT','FIRMA','AUTENT',
                           'DOCUM','DNI','RUC','CLTE','TARJE','CVV','PAN']
}

# ── Hash determinista para datos ficticios reproducibles ────────────────────
def det_hash(seed: str, min_val: int, max_val: int) -> int:
    """Genera un entero pseudo-aleatorio determinista en [min_val, max_val]."""
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return min_val + (h % (max_val - min_val + 1))


def level(score: int) -> str:
    if score >= 75: return 'ALTO'
    if score >= 50: return 'MEDIO'
    if score >= 25: return 'BAJO'
    return 'MUY_BAJO'


# ── EJE 1: Deuda Técnica ────────────────────────────────────────────────────
def calc_technical_debt(meta: dict, src_lines: list) -> int:
    """
    Indicadores:
    · Sin autor ni fecha de escritura
    · LOC muy alto sin documentación (>2000 sin descripción)
    · Sin copybooks (lógica no reutilizable)
    · Sin códigos de retorno documentados
    · Presencia de patrones legacy (GOTO detectado en source)
    · Fecha de escritura muy antigua (anterior a 2000)
    · Sin descripción del programa
    """
    score = 0
    loc         = meta.get('loc', 0) or 0
    author      = meta.get('author', '') or ''
    date_wr     = meta.get('date_written', '') or ''
    description = meta.get('description', '') or ''
    cb_count    = meta.get('copybook_count', 0) or 0
    ret_codes   = meta.get('return_codes', []) or []
    last_mod    = meta.get('last_modification', '') or ''
    comments    = meta.get('comments', []) or []

    # Sin autor
    if not author.strip():
        score += 15

    # Sin fecha de escritura
    if not date_wr.strip():
        score += 10
    else:
        # Año < 2000 → código legacy antiguo
        year_match = None
        m = re.search(r'(\d{4})', date_wr)
        if m:
            yr = int(m.group(1))
            if yr < 2000:
                score += 15
            elif yr < 2010:
                score += 7

    # Sin descripción
    if not description.strip() or len(description.strip()) < 10:
        score += 10

    # LOC muy alto → difícil de mantener
    if loc > 5000:
        score += 20
    elif loc > 2000:
        score += 10
    elif loc > 1000:
        score += 5

    # Sin copybooks (lógica duplicada)
    if cb_count == 0 and loc > 100:
        score += 10

    # Sin códigos de retorno documentados
    if not ret_codes:
        score += 10

    # Sin modificaciones recientes
    if not last_mod.strip():
        score += 5

    # GOTO en el código fuente
    goto_count = sum(1 for ln in src_lines if re.search(r'\bGO\s+TO\b', ln, re.IGNORECASE))
    if goto_count > 5:
        score += 15
    elif goto_count > 0:
        score += 7

    return min(score, 100)


def explain_technical_debt(meta: dict, src_lines: list, score: int) -> str:
    """Texto explicativo de los factores que determinaron technical_debt_score."""
    loc       = meta.get('loc', 0) or 0
    author    = meta.get('author', '') or ''
    date_wr   = meta.get('date_written', '') or ''
    descr     = meta.get('description', '') or ''
    cb_count  = meta.get('copybook_count', 0) or 0
    ret_codes = meta.get('return_codes', []) or []
    last_mod  = meta.get('last_modification', '') or ''

    factors = []

    if not author.strip():
        factors.append("Sin autor documentado: +15 puntos (dificulta la gestión del código).")
    else:
        factors.append(f"Autor documentado ({author.strip()}): 0 puntos.")

    if not date_wr.strip():
        factors.append("Sin fecha de escritura: +10 puntos (origen desconocido).")
    else:
        m = re.search(r'(\d{4})', date_wr)
        if m:
            yr = int(m.group(1))
            if yr < 2000:
                factors.append(f"Código escrito en {yr} (anterior a 2000, legacy muy antiguo): +15 puntos.")
            elif yr < 2010:
                factors.append(f"Código escrito en {yr} (anterior a 2010): +7 puntos.")
            else:
                factors.append(f"Código escrito en {yr}: 0 puntos.")

    if not descr.strip() or len(descr.strip()) < 10:
        factors.append("Sin descripción funcional: +10 puntos (bajo nivel de documentación).")
    else:
        factors.append("Tiene descripción funcional: 0 puntos.")

    if loc > 5000:
        factors.append(f"Muy alto volumen de código ({loc} LOC): +20 puntos (extremadamente difícil de mantener).")
    elif loc > 2000:
        factors.append(f"Alto volumen de código ({loc} LOC): +10 puntos (difícil de mantener).")
    elif loc > 1000:
        factors.append(f"Volumen de código moderado ({loc} LOC): +5 puntos.")
    else:
        factors.append(f"Volumen de código bajo ({loc} LOC): 0 puntos.")

    if cb_count == 0 and loc > 100:
        factors.append("Sin copybooks: +10 puntos (lógica probablemente duplicada, no reutilizable).")
    else:
        factors.append(f"Usa {cb_count} copybook(s): 0 puntos (código reutilizable).")

    if not ret_codes:
        factors.append("Sin códigos de retorno documentados: +10 puntos (comportamiento ante errores desconocido).")
    else:
        factors.append(f"Tiene {len(ret_codes)} código(s) de retorno documentados: 0 puntos.")

    if not last_mod.strip():
        factors.append("Sin registro de última modificación: +5 puntos.")

    goto_count = sum(1 for ln in src_lines if re.search(r'\bGO\s+TO\b', ln, re.IGNORECASE))
    if goto_count > 5:
        factors.append(f"Contiene {goto_count} sentencias GO TO: +15 puntos (patrón legacy de difícil mantenimiento).")
    elif goto_count > 0:
        factors.append(f"Contiene {goto_count} sentencia(s) GO TO: +7 puntos.")
    else:
        factors.append("Sin sentencias GO TO: 0 puntos.")

    return (f"Deuda Técnica {score}/100 — factores considerados: "
            + " | ".join(factors))


# ── EJE 2: Complejidad ──────────────────────────────────────────────────────
def calc_complexity(meta: dict, src_lines: list) -> int:
    """
    Indicadores:
    · LOC alto
    · Número de rutinas llamadas (call_count)
    · Número de copybooks
    · Número de layouts embebidos
    · Número de tablas DB2 accedidas
    · Número de datasets I/O
    · Complejidad ciclomática (IF/WHEN/EVALUATE en source)
    """
    score = 0
    loc         = meta.get('loc', 0) or 0
    call_count  = meta.get('call_count', 0) or 0
    cb_count    = meta.get('copybook_count', 0) or 0
    layout_count= meta.get('layout_count', 0) or 0
    db2_tables  = meta.get('exec_sql_tables', []) or []
    db2_incl    = meta.get('exec_sql_includes', []) or []
    in_ds       = meta.get('input_count', 0) or 0
    out_ds      = meta.get('output_count', 0) or 0

    # LOC
    if loc > 5000:   score += 25
    elif loc > 2000: score += 18
    elif loc > 1000: score += 12
    elif loc > 500:  score += 7
    else:            score += 3

    # Llamadas a rutinas
    score += min(call_count * 4, 20)

    # Copybooks
    score += min(cb_count * 3, 12)

    # Layouts embebidos
    score += min(layout_count * 2, 10)

    # DB2
    n_db2 = len(set(db2_tables) | set(db2_incl))
    score += min(n_db2 * 2, 15)

    # Datasets
    score += min((in_ds + out_ds) * 3, 12)

    # Complejidad ciclomática del fuente (IF/WHEN/EVALUATE)
    cyclo = sum(1 for ln in src_lines
                if re.search(r'\b(IF|WHEN|EVALUATE|PERFORM\s+UNTIL|UNTIL)\b', ln, re.IGNORECASE))
    if cyclo > 100:   score += 15
    elif cyclo > 50:  score += 10
    elif cyclo > 20:  score += 5

    return min(score, 100)


def explain_complexity(meta: dict, src_lines: list, score: int) -> str:
    """Texto explicativo de los factores que determinaron complexity_score."""
    loc          = meta.get('loc', 0) or 0
    call_count   = meta.get('call_count', 0) or 0
    cb_count     = meta.get('copybook_count', 0) or 0
    layout_count = meta.get('layout_count', 0) or 0
    db2_tables   = meta.get('exec_sql_tables', []) or []
    db2_incl     = meta.get('exec_sql_includes', []) or []
    in_ds        = meta.get('input_count', 0) or 0
    out_ds       = meta.get('output_count', 0) or 0
    n_db2        = len(set(db2_tables) | set(db2_incl))

    factors = []

    if loc > 5000:        pts = 25
    elif loc > 2000:      pts = 18
    elif loc > 1000:      pts = 12
    elif loc > 500:       pts = 7
    else:                 pts = 3
    factors.append(f"{loc} líneas de código: +{pts} puntos.")

    pts = min(call_count * 4, 20)
    if call_count > 0:
        factors.append(f"Llama a {call_count} rutina(s): +{pts} puntos.")
    else:
        factors.append("Sin llamadas a rutinas: +0 puntos.")

    pts = min(cb_count * 3, 12)
    if cb_count > 0:
        factors.append(f"Usa {cb_count} copybook(s): +{pts} puntos (más estructuras de datos a gestionar).")
    else:
        factors.append("Sin copybooks: +0 puntos.")

    pts = min(layout_count * 2, 10)
    if layout_count > 0:
        factors.append(f"{layout_count} layout(s) embebido(s): +{pts} puntos.")

    pts = min(n_db2 * 2, 15)
    if n_db2 > 0:
        factors.append(f"Accede a {n_db2} tabla(s) DB2: +{pts} puntos.")
    else:
        factors.append("Sin acceso a DB2: +0 puntos.")

    pts = min((in_ds + out_ds) * 3, 12)
    if in_ds + out_ds > 0:
        factors.append(f"{in_ds} dataset(s) entrada + {out_ds} salida: +{pts} puntos.")

    cyclo = sum(1 for ln in src_lines
                if re.search(r'\b(IF|WHEN|EVALUATE|PERFORM\s+UNTIL|UNTIL)\b', ln, re.IGNORECASE))
    if cyclo > 100:        pts = 15
    elif cyclo > 50:       pts = 10
    elif cyclo > 20:       pts = 5
    else:                  pts = 0
    factors.append(f"Complejidad ciclomática: {cyclo} ramificaciones (IF/WHEN/EVALUATE): +{pts} puntos.")

    return (f"Complejidad {score}/100 — factores considerados: "
            + " | ".join(factors))


# ── EJE 3: Valor de Negocio ─────────────────────────────────────────────────
def calc_business_value(meta: dict, program_id: str) -> int:
    """
    Indicadores:
    · Accede a muchas tablas DB2 → procesa datos críticos
    · Genera datasets de salida (produce información)
    · Tiene descripción que menciona palabras clave de negocio
    · Es llamado por muchos programas (alta reutilización)
    · Nombre del programa sugiere proceso crítico
    """
    score = 0
    db2_tables  = meta.get('exec_sql_tables', []) or []
    db2_incl    = meta.get('exec_sql_includes', []) or []
    out_ds      = meta.get('output_count', 0) or 0
    in_ds       = meta.get('input_count', 0) or 0
    description = (meta.get('description', '') or '').upper()
    ret_codes   = meta.get('return_codes', []) or []
    comments    = ' '.join(meta.get('comments', []) or []).upper()

    # DB2 → operaciones transaccionales
    n_db2 = len(set(db2_tables) | set(db2_incl))
    score += min(n_db2 * 3, 30)

    # Genera salidas (produce información para el negocio)
    score += min(out_ds * 6, 18)
    score += min(in_ds * 3, 12)

    # Descripción menciona procesos críticos de negocio
    all_text = description + ' ' + comments
    for kw in CRITICAL_KEYWORDS['keywords']:
        if kw in all_text:
            score += 8
            break

    for kw in CRITICAL_KEYWORDS['db2_critical']:
        for tbl in (db2_tables + db2_incl):
            if kw in tbl.upper():
                score += 5
                break
        else:
            continue
        break

    # Tiene códigos de retorno (bien documentado = negocio lo conoce)
    if ret_codes:
        score += 8

    # Nombre del programa sugiere criticidad
    pid_upper = program_id.upper()
    for kw in CRITICAL_KEYWORDS['keywords']:
        if kw[:3] in pid_upper:
            score += 5
            break

    return min(score, 100)


def explain_business_value(meta: dict, program_id: str, score: int) -> str:
    """
    Genera un texto explicativo con los factores concretos que determinaron
    el business_value_score de un programa COBOL.
    """
    db2_tables  = meta.get('exec_sql_tables', []) or []
    db2_incl    = meta.get('exec_sql_includes', []) or []
    out_ds      = meta.get('output_count', 0) or 0
    in_ds       = meta.get('input_count', 0) or 0
    description = (meta.get('description', '') or '').upper()
    ret_codes   = meta.get('return_codes', []) or []
    comments    = ' '.join(meta.get('comments', []) or []).upper()
    pid_upper   = program_id.upper()

    n_db2 = len(set(db2_tables) | set(db2_incl))
    all_text = description + ' ' + comments

    factors = []

    # DB2
    if n_db2 == 0:
        factors.append(f"No accede a tablas DB2 (0 puntos de los 30 posibles por operaciones transaccionales).")
    else:
        pts = min(n_db2 * 3, 30)
        tbls = ', '.join(list(set(db2_tables + db2_incl))[:5])
        factors.append(f"Accede a {n_db2} tabla(s) DB2 ({tbls}): +{pts} puntos por procesamiento transaccional.")

    # Datasets de salida
    if out_ds == 0:
        factors.append("No genera datasets de salida (0 puntos de los 18 posibles por producción de información).")
    else:
        pts = min(out_ds * 6, 18)
        factors.append(f"Genera {out_ds} dataset(s) de salida: +{pts} puntos por producción de información para el negocio.")

    # Datasets de entrada
    if in_ds > 0:
        pts = min(in_ds * 3, 12)
        factors.append(f"Consume {in_ds} dataset(s) de entrada: +{pts} puntos.")

    # Palabras clave de negocio en descripción/comentarios
    found_kw = [kw for kw in CRITICAL_KEYWORDS['keywords'] if kw in all_text]
    if found_kw:
        factors.append(f"La descripción/comentarios contienen palabras clave de negocio crítico ({', '.join(found_kw[:3])}): +8 puntos.")
    else:
        factors.append("La descripción/comentarios no contienen palabras clave de procesos críticos de negocio (0 puntos).")

    # Tablas DB2 críticas
    db2_crit_found = []
    for kw in CRITICAL_KEYWORDS['db2_critical']:
        for tbl in (db2_tables + db2_incl):
            if kw in tbl.upper():
                db2_crit_found.append(tbl)
                break
    if db2_crit_found:
        factors.append(f"Accede a tablas DB2 con datos críticos ({db2_crit_found[0]}): +5 puntos.")
    else:
        factors.append("Las tablas DB2 no contienen nombres asociados a datos críticos de negocio (0 puntos).")

    # Códigos de retorno
    if ret_codes:
        factors.append(f"Tiene {len(ret_codes)} código(s) de retorno documentados: +8 puntos (programa conocido por el negocio).")
    else:
        factors.append("Sin códigos de retorno documentados: 0 puntos (bajo reconocimiento por el negocio).")

    # Nombre del programa
    name_kw = [kw for kw in CRITICAL_KEYWORDS['keywords'] if kw[:3] in pid_upper]
    if name_kw:
        factors.append(f"El nombre del programa ({program_id}) sugiere proceso crítico ({name_kw[0]}): +5 puntos.")
    else:
        factors.append(f"El nombre del programa ({program_id}) no sugiere proceso crítico de negocio (0 puntos).")

    rationale = (
        f"Valor de Negocio {score}/100 — factores considerados: "
        + " | ".join(factors)
    )
    return rationale


# ── EJE 4: Riesgo de Seguridad ──────────────────────────────────────────────
def calc_security_risk(meta: dict, src_lines: list, program_id: str) -> int:
    """
    Indicadores:
    · Accede a tablas DB2 con datos sensibles
    · Maneja campos con nombres sensibles (clave, password, token, tarjeta...)
    · Realiza UPDATE/DELETE/INSERT en DB2
    · Lee/escribe datasets con nombres sensibles
    · Sin validación de SQLCODE (mal manejo de errores DB2)
    · Usa CALL DYNAMIC (ejecución dinámica → inyección posible)
    """
    import re
    score = 0
    db2_tables  = meta.get('exec_sql_tables', []) or []
    db2_incl    = meta.get('exec_sql_includes', []) or []
    in_ds       = meta.get('input_datasets', []) or []
    out_ds      = meta.get('output_datasets', []) or []
    ret_codes   = meta.get('return_codes', []) or []

    # Tablas DB2 con nombres sensibles
    all_db2 = [t.upper() for t in (db2_tables + db2_incl)]
    for tbl in all_db2:
        for kw in CRITICAL_KEYWORDS['security_sensitive']:
            if kw in tbl:
                score += 8
                break

    # Sentencias SQL de modificación en el fuente
    dml_count = sum(1 for ln in src_lines
                    if re.search(r'\b(INSERT|UPDATE|DELETE|MERGE)\b', ln, re.IGNORECASE))
    if dml_count > 20:   score += 25
    elif dml_count > 5:  score += 15
    elif dml_count > 0:  score += 8

    # Accede a DB2 sin manejo de SQLCODE (riesgo de error silencioso)
    has_db2     = bool(db2_tables or db2_incl)
    has_sqlcode = any(re.search(r'\bSQLCODE\b', ln, re.IGNORECASE) for ln in src_lines)
    if has_db2 and not has_sqlcode:
        score += 15

    # Datasets con nombres sensibles
    all_ds = [d.upper() for d in (in_ds + out_ds)]
    for dsn in all_ds:
        for kw in CRITICAL_KEYWORDS['security_sensitive']:
            if kw in dsn:
                score += 5
                break

    # CALL dinámico (inyección de código)
    dynamic_calls = sum(1 for ln in src_lines
                        if re.search(r'\bCALL\s+\w+-\w+\b', ln, re.IGNORECASE)
                        and not re.search(r"CALL\s+'", ln))
    if dynamic_calls > 0:
        score += 10

    return min(score, 100)


def explain_security_risk(meta: dict, src_lines: list, program_id: str, score: int) -> str:
    """Texto explicativo de los factores que determinaron security_risk_score."""
    db2_tables = meta.get('exec_sql_tables', []) or []
    db2_incl   = meta.get('exec_sql_includes', []) or []
    in_ds_list = meta.get('input_datasets', []) or []
    out_ds_list= meta.get('output_datasets', []) or []

    factors = []

    # Tablas DB2 con nombres sensibles
    all_db2 = [t.upper() for t in (db2_tables + db2_incl)]
    sens_tbls = [t for t in all_db2
                 for kw in CRITICAL_KEYWORDS['security_sensitive'] if kw in t]
    if sens_tbls:
        factors.append(f"Tabla(s) DB2 con datos sensibles ({', '.join(sens_tbls[:3])}): "
                       f"+{min(len(sens_tbls)*8, 32)} puntos.")
    else:
        factors.append("Sin tablas DB2 con nombres sensibles: +0 puntos.")

    # Sentencias DML
    dml_count = sum(1 for ln in src_lines
                    if re.search(r'\b(INSERT|UPDATE|DELETE|MERGE)\b', ln, re.IGNORECASE))
    if dml_count > 20:   pts = 25
    elif dml_count > 5:  pts = 15
    elif dml_count > 0:  pts = 8
    else:                pts = 0
    if dml_count > 0:
        factors.append(f"{dml_count} sentencia(s) SQL de modificacion (INSERT/UPDATE/DELETE): "
                       f"+{pts} puntos (mayor superficie de ataque sobre datos).")
    else:
        factors.append("Sin sentencias SQL de modificacion (INSERT/UPDATE/DELETE): +0 puntos.")

    # SQLCODE
    has_db2     = bool(db2_tables or db2_incl)
    has_sqlcode = any(re.search(r'\bSQLCODE\b', ln, re.IGNORECASE) for ln in src_lines)
    if has_db2 and not has_sqlcode:
        factors.append("Accede a DB2 pero no valida SQLCODE: +15 puntos "
                       "(errores DB2 silenciosos = riesgo de integridad y seguridad).")
    elif has_db2:
        factors.append("Accede a DB2 y valida SQLCODE: +0 puntos.")
    else:
        factors.append("Sin acceso a DB2: +0 puntos por SQLCODE.")

    # Datasets sensibles
    all_ds = [d.upper() for d in (in_ds_list + out_ds_list)]
    sens_ds = [d for d in all_ds
               for kw in CRITICAL_KEYWORDS['security_sensitive'] if kw in d]
    if sens_ds:
        factors.append(f"Dataset(s) con nombres sensibles ({', '.join(sens_ds[:3])}): "
                       f"+{min(len(sens_ds)*5, 20)} puntos.")
    else:
        factors.append("Sin datasets con nombres sensibles: +0 puntos.")

    # CALL dinámico
    dynamic_calls = sum(1 for ln in src_lines
                        if re.search(r'\bCALL\s+\w+-\w+\b', ln, re.IGNORECASE)
                        and not re.search(r"CALL\s+'", ln))
    if dynamic_calls > 0:
        factors.append(f"{dynamic_calls} CALL(s) dinamico(s): +10 puntos "
                       "(ejecucion dinamica puede permitir inyeccion de logica).")
    else:
        factors.append("Sin CALLs dinamicos: +0 puntos.")

    return (f"Riesgo de Seguridad {score}/100 — factores considerados: "
            + " | ".join(factors))


# ── EJE 5: Tiempo de Procesamiento (ficticio/simulado) ──────────────────────
def calc_processing_time(meta: dict, program_id: str) -> int:
    """
    Tiempo simulado en segundos, basado en características del programa.
    Determinista por program_id (reproducible).
    Rangos típicos mainframe:
      · Programa simple (< 500 LOC, sin DB2)   :  10 –  90 s
      · Programa medio  (500-2000 LOC)          :  60 – 300 s
      · Programa grande (> 2000 LOC + DB2)      : 180 – 900 s
    """
    loc        = meta.get('loc', 100) or 100
    db2_tables = meta.get('exec_sql_tables', []) or []
    db2_incl   = meta.get('exec_sql_includes', []) or []
    in_ds      = meta.get('input_count', 0) or 0
    out_ds     = meta.get('output_count', 0) or 0
    call_count = meta.get('call_count', 0) or 0
    n_db2      = len(set(db2_tables) | set(db2_incl))
    has_db2    = n_db2 > 0

    if loc > 2000 or has_db2:
        base = det_hash(program_id + '.base', 180, 600)
    elif loc > 500:
        base = det_hash(program_id + '.base', 60, 240)
    else:
        base = det_hash(program_id + '.base', 10, 90)

    # Variacion por caracteristicas
    extra  = n_db2 * det_hash(program_id + '.db2', 5, 20)
    extra += (in_ds + out_ds) * det_hash(program_id + '.ds', 2, 10)
    extra += call_count * det_hash(program_id + '.call', 1, 8)

    return base + extra


def explain_processing_time(meta: dict, program_id: str, time_sec: int) -> str:
    """Texto explicativo de los factores que determinaron processing_time_sec."""
    loc        = meta.get('loc', 100) or 100
    db2_tables = meta.get('exec_sql_tables', []) or []
    db2_incl   = meta.get('exec_sql_includes', []) or []
    in_ds      = meta.get('input_count', 0) or 0
    out_ds     = meta.get('output_count', 0) or 0
    call_count = meta.get('call_count', 0) or 0
    n_db2      = len(set(db2_tables) | set(db2_incl))
    has_db2    = n_db2 > 0

    factors = []

    if loc > 2000 or has_db2:
        factors.append(f"Programa grande ({loc} LOC) o con acceso DB2 → rango base 180-600s.")
    elif loc > 500:
        factors.append(f"Programa mediano ({loc} LOC, sin DB2) → rango base 60-240s.")
    else:
        factors.append(f"Programa simple ({loc} LOC, sin DB2) → rango base 10-90s.")

    if n_db2 > 0:
        extra_db2 = n_db2 * det_hash(program_id + '.db2', 5, 20)
        factors.append(f"{n_db2} tabla(s) DB2 accedidas: +{extra_db2}s estimados "
                       "(cada acceso DB2 agrega latencia de I/O).")

    if in_ds + out_ds > 0:
        extra_ds = (in_ds + out_ds) * det_hash(program_id + '.ds', 2, 10)
        factors.append(f"{in_ds} dataset(s) entrada + {out_ds} salida: "
                       f"+{extra_ds}s estimados (I/O secuencial mainframe).")

    if call_count > 0:
        extra_call = call_count * det_hash(program_id + '.call', 1, 8)
        factors.append(f"{call_count} llamada(s) a rutinas externas: "
                       f"+{extra_call}s estimados (overhead de linkage/subprogramas).")

    factors.append("Nota: tiempo estimado/simulado deterministicamente por "
                   "caracteristicas del programa, no medido en produccion.")

    return (f"Tiempo de Proceso {time_sec}s — factores considerados: "
            + " | ".join(factors))


# ── Leer líneas fuente COBOL (si está disponible en el workspace) ────────────
def load_source_lines(program_id: str, src_dir: Path) -> list:
    """Busca el archivo .cbl del programa en src_dir y su padre."""
    for search_dir in [src_dir, src_dir.parent]:
        for name in [program_id + '.cbl', program_id + '.CBL', program_id]:
            p = search_dir / name
            if p.exists():
                try:
                    return p.read_text(encoding='latin-1', errors='ignore').splitlines()
                except Exception:
                    pass
    return []


# ── Pipeline principal ───────────────────────────────────────────────────────
def run(input_dir: Path, output_dir: Path):
    rag_in  = input_dir  / 'rag_cobol.jsonl'
    rag_out = output_dir / 'rag_cobol_enriched.jsonl'
    axes_out = output_dir / 'program_axes.csv'

    if not rag_in.exists():
        print(f"ERROR: No se encontró {rag_in}")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Leer todos los documentos
    docs = []
    with open(rag_in, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))

    print(f"Documentos RAG leídos   : {len(docs)}")

    axes_rows = []
    enriched  = []

    for doc in docs:
        meta = doc.get('metadata', {})
        doc_type = meta.get('type', '')

        # Solo enriquecemos los documentos de tipo cobol_program
        # Los demás se pasan sin cambio
        if doc_type != 'cobol_program':
            enriched.append(doc)
            continue

        program_id = meta.get('program_id', doc['id'])
        src_lines  = load_source_lines(program_id, input_dir)

        debt_score  = calc_technical_debt(meta, src_lines)
        comp_score  = calc_complexity(meta, src_lines)
        bv_score    = calc_business_value(meta, program_id)
        sec_score   = calc_security_risk(meta, src_lines, program_id)
        time_sec    = calc_processing_time(meta, program_id)

        debt_lvl = level(debt_score); comp_lvl = level(comp_score)
        bv_lvl   = level(bv_score);   sec_lvl  = level(sec_score)

        debt_rationale = explain_technical_debt(meta, src_lines, debt_score)
        comp_rationale = explain_complexity(meta, src_lines, comp_score)
        bv_rationale   = explain_business_value(meta, program_id, bv_score)
        sec_rationale  = explain_security_risk(meta, src_lines, program_id, sec_score)
        time_rationale = explain_processing_time(meta, program_id, time_sec)

        # Añadir al metadata
        meta['technical_debt_score']      = debt_score
        meta['technical_debt_level']      = debt_lvl
        meta['technical_debt_rationale']  = debt_rationale
        meta['complexity_score']          = comp_score
        meta['complexity_level']          = comp_lvl
        meta['complexity_rationale']      = comp_rationale
        meta['business_value_score']      = bv_score
        meta['business_value_level']      = bv_lvl
        meta['business_value_rationale']  = bv_rationale
        meta['security_risk_score']       = sec_score
        meta['security_risk_level']       = sec_lvl
        meta['security_risk_rationale']   = sec_rationale
        meta['processing_time_sec']       = time_sec
        meta['processing_time_rationale'] = time_rationale

        # Enriquecer el texto con resumen de ejes
        axis_summary = (
            f" [ANALISIS] Deuda tecnica: {debt_score}/100 ({debt_lvl}). "
            f"Complejidad: {comp_score}/100 ({comp_lvl}). "
            f"Valor negocio: {bv_score}/100 ({bv_lvl}). "
            f"Riesgo seguridad: {sec_score}/100 ({sec_lvl}). "
            f"Tiempo procesamiento estimado: {time_sec}s."
        )
        doc['text'] = doc.get('text', '') + axis_summary

        enriched.append(doc)

        axes_rows.append({
            'program_id':                 program_id,
            'technical_debt_score':       debt_score,
            'technical_debt_level':       debt_lvl,
            'technical_debt_rationale':   debt_rationale,
            'complexity_score':           comp_score,
            'complexity_level':           comp_lvl,
            'complexity_rationale':       comp_rationale,
            'business_value_score':       bv_score,
            'business_value_level':       bv_lvl,
            'business_value_rationale':   bv_rationale,
            'security_risk_score':        sec_score,
            'security_risk_level':        sec_lvl,
            'security_risk_rationale':    sec_rationale,
            'processing_time_sec':        time_sec,
            'processing_time_rationale':  time_rationale,
        })

    # Escribir JSONL enriquecido
    with open(rag_out, 'w', encoding='utf-8') as f:
        for doc in enriched:
            f.write(json.dumps(doc, ensure_ascii=False) + '\n')
    print(f"JSONL enriquecido escrito: {rag_out}  ({len(enriched)} documentos)")

    # Escribir CSV de ejes
    if axes_rows:
        fieldnames = list(axes_rows[0].keys())
        with open(axes_out, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(axes_rows)
        print(f"CSV de ejes escrito     : {axes_out}  ({len(axes_rows)} programas)")

    # Resumen por nivel
    if axes_rows:
        print("\n=== RESUMEN DE EJES (programas cobol_program) ===")
        for eje, col in [('Deuda Técnica', 'technical_debt_level'),
                         ('Complejidad',    'complexity_level'),
                         ('Valor Negocio',  'business_value_level'),
                         ('Riesgo Segur.',  'security_risk_level')]:
            counts = {}
            for r in axes_rows:
                lv = r[col]
                counts[lv] = counts.get(lv, 0) + 1
            summary = '  |  '.join(f"{lv}: {n}" for lv, n in sorted(counts.items()))
            print(f"  {eje:18s}: {summary}")

        avg_time = sum(r['processing_time_sec'] for r in axes_rows) / len(axes_rows)
        max_time = max(r['processing_time_sec'] for r in axes_rows)
        print(f"  {'Tiempo Proc.(avg)':18s}: {avg_time:.0f}s  (max: {max_time}s)")

    print("\nCypher para agregar ejes en Neo4j Browser (una vez cargado program_axes.csv):")
    print("  LOAD CSV WITH HEADERS FROM 'file:///program_axes.csv' AS row")
    print("  MATCH (p:Program {program_id: row.program_id})")
    print("  SET p.technical_debt_score  = toInteger(row.technical_debt_score),")
    print("      p.technical_debt_level  = row.technical_debt_level,")
    print("      p.complexity_score      = toInteger(row.complexity_score),")
    print("      p.complexity_level      = row.complexity_level,")
    print("      p.business_value_score  = toInteger(row.business_value_score),")
    print("      p.business_value_level  = row.business_value_level,")
    print("      p.security_risk_score   = toInteger(row.security_risk_score),")
    print("      p.security_risk_level   = row.security_risk_level,")
    print("      p.processing_time_sec   = toInteger(row.processing_time_sec)")
    print("  RETURN count(p);")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Enriquece rag_cobol.jsonl con 5 ejes de análisis.')
    parser.add_argument('--input-dir',  default=str(DEFAULT_DIR), help='Directorio con rag_cobol.jsonl')
    parser.add_argument('--output-dir', default=str(DEFAULT_DIR), help='Directorio de salida')
    args = parser.parse_args()

    run(Path(args.input_dir), Path(args.output_dir))
