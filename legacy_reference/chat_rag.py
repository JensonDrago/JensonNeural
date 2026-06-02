"""
GraphRAG Chat  —  COBOL + JCL  |  OpenAI + Neo4j + Gradio
===========================================================
Interfaz web de chat que combina búsqueda vectorial y navegación
de grafo sobre el inventario COBOL/JCL del mainframe:

  1. Genera embedding de la pregunta  (text-embedding-3-small)
  2. Busca los documentos RAG más similares en Neo4j AuraDB
     usando un índice vectorial coseno (VECTOR_SEARCH)
  3. Amplía el contexto navegando relaciones del grafo
     (programas → datasets, copybooks, rutinas, tablas DB2)
  4. Envía el contexto enriquecido + pregunta a GPT-4o
  5. Muestra respuesta + grafo visual de dependencias en Gradio

Prerequisitos:
  pip install gradio neo4j requests

Uso:
  python chat_rag.py
  Abre http://localhost:7860 en el navegador
"""

import json
import re
import sys
from pathlib import Path
import requests
from neo4j import GraphDatabase
import gradio as gr

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from graphrag_impact import collect_impact_context, has_impact_intent

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACION  —  edita estos valores
# ══════════════════════════════════════════════════════════════════════════════
NEO4J_URI      = "neo4j+s://885c1ddb.databases.neo4j.io"
NEO4J_USER     = "885c1ddb"
NEO4J_PASSWORD = "dmGUGrtqiP5BAi3IsW4aa-z0Van5lwQljUjKONgmZBA"                  # <-- ajustar si cambiaste la contraseña

# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY    = "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"
OPENAI_BASE_URL   = "https://eu.api.openai.com/v1"   # endpoint Europa
OPENAI_CHAT_MODEL = "gpt-4o"                          # modelo de chat
OPENAI_EMBED_MODEL= "text-embedding-3-small"          # 1536 dims — debe coincidir con el índice Neo4j
TOP_K          = 8                         # documentos RAG a recuperar por consulta

# ══════════════════════════════════════════════════════════════════════════════
# PROMPT DE SISTEMA
# ══════════════════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """\
Eres un experto en sistemas mainframe IBM (COBOL, JCL, DB2, VSAM, CICS).
Tu única fuente de hechos es el bloque "=== DATOS DEL GRAFO ===".

REGLAS ESTRICTAS:
1. SOLO menciona nombres de programas, JOBs, datasets, rutinas, copybooks o tablas DB2
   o elementos de malla que aparezcan LITERALMENTE en ese bloque. Nunca inventes ni deduzcas nombres.
2. Puedes y DEBES razonar sobre las relaciones y evidencias que el grafo expone:
   - Encadenamiento de llamadas (A llama a B que llama a C)
   - Flujo de datos (quién lee/escribe cada dataset)
   - Impacto de cambios (si cambia X, qué programas o JOBs se ven afectados)
   - Impacto aguas arriba/abajo (productores y consumidores por datasets,
     JOB.step que ejecutan programas, y programas que acceden tablas DB2)
   - Mallas Control-M: condiciones de entrada/salida, predecesores,
     sucesores y vínculo entre objeto de malla y JOB JCL.
   - Operaciones SQL: si un programa tiene códigos de retorno que mencionan
     COMMIT/ROLLBACK/SELECT/UPDATE/INSERT/DELETE, o constantes/switches con esos
     nombres (CTE-ROLLBACK, SW-ROLLBACK, CTE-COMMIT...), el programa EJECUTA
     esas sentencias SQL DB2.
   - Si el campo de "Codigos retorno" menciona una operación (ej: "ERROR AL HACER
     COMMIT O ROLLBACK"), eso confirma que el programa usa esa operación.
3. Cuando no encuentres coincidencia EXACTA pero sí evidencia indirecta (constantes,
   switches, códigos de retorno), respóndela marcando con "[Inferencia]:".
4. Si el usuario menciona un valor numérico que NO coincide con el que aparece en el
   bloque, corrígelo amablemente usando el valor real del grafo y explica ese valor.
   Ejemplo: usuario dice "18/100" pero el grafo dice "53/100" → responde con el
   valor correcto (53) y explica por qué es ese valor.
5. SOLO si el bloque no contiene absolutamente ninguna información relevante sobre
   el elemento consultado, responde:
   "No encontré información sobre eso en el grafo."
6. Responde en español. Usa listas o tablas. Nombres de sistemas en MAYÚSCULAS.

7. Si la consulta pide optimización, migración, modernización, priorización,
   candidatos, plan de ejecución o decisión estratégica, responde usando la
   "Matriz de Decisión Estratégica" definida en el contexto. La recomendación
   debe mapear cada candidato a una decisión y mostrar explícitamente los 5 ejes:
   Valor de Negocio, Complejidad/Dependencias, Deuda Técnica, Consumo/Tiempo de
   Proceso Mainframe y Riesgo de Seguridad.
"""

STRATEGIC_DECISION_MATRIX = """\
=== MATRIZ DE DECISIÓN ESTRATÉGICA PARA MODERNIZACIÓN MAINFRAME ===

Usa esta matriz SOLO como marco de decisión. Los candidatos, scores y evidencias
deben salir del grafo.

1. Migración Temprana / Quick Wins (Replatforming o Rehosting)
- Valor de Negocio: MEDIO/ALTO.
- Complejidad y Dependencias: BAJA.
- Deuda Técnica: BAJA/MEDIA.
- Consumo/Tiempo de Proceso Mainframe: MEDIO/ALTO.
- Riesgo de Seguridad: BAJO.
- Uso recomendado: módulos batch periféricos, aislados, con bajo acoplamiento,
  que reduzcan MIPS/costos y permitan validar la arquitectura cloud rápidamente.

2. Refactorización Estratégica (Rearchitecting / Cloud-Native)
- Valor de Negocio: ALTO.
- Complejidad y Dependencias: MEDIA/ALTA.
- Deuda Técnica: ALTA.
- Consumo/Tiempo de Proceso Mainframe: ALTO.
- Riesgo de Seguridad: MEDIO/ALTO.
- Uso recomendado: sistemas estratégicos, complejos y de alto consumo donde la
  reescritura o descomposición por dominios entregue escalabilidad e innovación.

3. Reemplazo (Repurchasing / SaaS / Low-Code)
- Valor de Negocio: MEDIO/ALTO.
- Complejidad y Dependencias: BAJA/MEDIA.
- Deuda Técnica: MUY ALTA.
- Consumo/Tiempo de Proceso Mainframe: MEDIO/ALTO.
- Riesgo de Seguridad: BAJO/MEDIO.
- Uso recomendado: funcionalidades estándar de mercado donde mantener o
  refactorizar el legado no sea económicamente conveniente.

4. Encapsulamiento y Convivencia (Retain / API-First)
- Valor de Negocio: ALTO.
- Complejidad y Dependencias: MUY ALTA.
- Deuda Técnica: ALTA.
- Consumo/Tiempo de Proceso Mainframe: ALTO.
- Riesgo de Seguridad: ALTO.
- Uso recomendado: sistemas centrales, transaccionales o fuertemente acoplados
  que deben permanecer en mainframe pero exponerse por APIs, CQRS o caché.

Formato obligatorio para respuestas estratégicas:
- Primero una tabla con columnas:
  Candidato | Decisión recomendada | Valor Negocio | Complejidad/Dependencias |
  Deuda Técnica | Consumo/Tiempo Mainframe | Riesgo Seguridad | Evidencia del grafo.
- Después una sección breve "Justificación" con el porqué de la decisión.
- Después una sección "Siguiente acción" con el primer paso recomendado.
- Si ningún candidato encaja perfecto, usa la opción más cercana y dilo
  explícitamente: "criterio aproximado".
"""

# ══════════════════════════════════════════════════════════════════════════════
# CONSULTAS NEO4J
# ══════════════════════════════════════════════════════════════════════════════
VECTOR_SEARCH = """
CALL db.index.vector.queryNodes('rag_cobol_index', $top_k, $query_vector)
YIELD node, score
RETURN node.id   AS doc_id,
       node.type AS doc_type,
       node.text AS doc_text,
       score
ORDER BY score DESC
"""

# Búsqueda directa por nombre de programa (sin pasar por RagDocument)
DIRECT_PROGRAM_SEARCH = """
UNWIND $keywords AS kw
MATCH (p:Program)
WHERE toUpper(p.program_id) = toUpper(kw) OR toUpper(p.name) = toUpper(kw)
WITH DISTINCT p
// colectar cada grupo por separado para evitar cross-join
OPTIONAL MATCH (p)-[:READS_DATASET]->(ds:Dataset)
WITH p, collect(DISTINCT ds.dd_name) AS reads_datasets
OPTIONAL MATCH (p)-[:WRITES_DATASET]->(dsw:Dataset)
WITH p, reads_datasets, collect(DISTINCT dsw.dd_name) AS writes_datasets
OPTIONAL MATCH (j:Job)-[:INVOKES]->(p)
WITH p, reads_datasets, writes_datasets, collect(DISTINCT j.job_id) AS invoked_by_jobs
OPTIONAL MATCH (p)-[:USES_COPYBOOK]->(c:Copybook)
WITH p, reads_datasets, writes_datasets, invoked_by_jobs, collect(DISTINCT c.name) AS copybooks
OPTIONAL MATCH (p)-[:ACCESSES_DB2]->(db2:DB2Table)
WITH p, reads_datasets, writes_datasets, invoked_by_jobs, copybooks, collect(DISTINCT db2.name) AS db2_tables
OPTIONAL MATCH (p)-[rc:CALLS]->(r:Routine)
WITH p, reads_datasets, writes_datasets, invoked_by_jobs, copybooks, db2_tables,
     [x IN collect(DISTINCT CASE WHEN rc.call_type='STATIC'  THEN r.name+' (x'+coalesce(toString(rc.call_count),'?')+')' END) WHERE x IS NOT NULL] AS static_routines,
     [x IN collect(DISTINCT CASE WHEN rc.call_type='DYNAMIC' THEN r.name+' (x'+coalesce(toString(rc.call_count),'?')+')' END) WHERE x IS NOT NULL] AS dynamic_routines
OPTIONAL MATCH (p)-[rl:HAS_LAYOUT]->(el:EmbeddedLayout)
WITH p, reads_datasets, writes_datasets, invoked_by_jobs, copybooks, db2_tables,
     static_routines, dynamic_routines,
     collect(DISTINCT el.name + ' (' + coalesce(rl.layout_type,'') + ')') AS layouts
RETURN p.program_id   AS program_id,
       p.loc           AS loc,
       p.author        AS author,
       p.date_written  AS date_written,
       p.app_name      AS app_name,
       reads_datasets, writes_datasets, invoked_by_jobs,
       copybooks, db2_tables, static_routines, dynamic_routines, layouts
"""

# Búsqueda directa por nombre de job
DIRECT_JOB_SEARCH = """
UNWIND $keywords AS kw
MATCH (j:Job)
WHERE toUpper(j.job_id) = toUpper(kw) OR toUpper(j.name) = toUpper(kw)
WITH DISTINCT j
OPTIONAL MATCH (j)-[:HAS_STEP]->(s:Step)
OPTIONAL MATCH (s)-[:EXECUTES]->(p:Program)
WITH j, s, p
ORDER BY s.sequence_number
WITH j,
     collect(coalesce(toString(s.sequence_number),'?') + '. ' +
             coalesce(s.step_name,'') + ' -> ' +
             coalesce(p.program_id,'(sin programa)')) AS step_programs,
     count(s) AS step_count
OPTIONAL MATCH (j)-[:HAS_STEP]->(sr:Step)-[:READS]->(jdr:JclDataset)
OPTIONAL MATCH (j)-[:HAS_STEP]->(sw:Step)-[:WRITES]->(jdw:JclDataset)
RETURN j.job_id                         AS job_id,
       step_count                        AS step_count,
       step_programs                     AS step_programs,
       collect(DISTINCT jdr.dsn_name)    AS reads_datasets,
       collect(DISTINCT jdw.dsn_name)    AS writes_datasets
"""

# Búsqueda directa por nombre de step (step_name, sin el prefijo job)
DIRECT_STEP_SEARCH = """
UNWIND $keywords AS kw
MATCH (s:Step)
WHERE toUpper(s.step_name) = toUpper(kw)
   OR toUpper(s.step_id)   = toUpper(kw)
MATCH (j:Job)-[:HAS_STEP]->(s)
OPTIONAL MATCH (s)-[:EXECUTES]->(p:Program)
OPTIONAL MATCH (s)-[:READS]->(jdr:JclDataset)
OPTIONAL MATCH (s)-[:WRITES]->(jdw:JclDataset)
RETURN DISTINCT s.step_name                          AS step_name,
       s.sequence_number                              AS sequence_number,
       s.exec_type                                    AS exec_type,
       s.activity_type                                AS activity_type,
       j.job_id                                       AS job_id,
       collect(DISTINCT p.program_id)                 AS executes_programs,
       collect(DISTINCT jdr.dsn_name)                 AS reads_datasets,
       collect(DISTINCT jdw.dsn_name)                 AS writes_datasets
"""

EXPAND_COBOL_PROGRAM = """
MATCH (d:RagDocument {id: $doc_id})-[:DOCUMENTS]->(p:Program)
OPTIONAL MATCH (p)-[:READS_DATASET]->(ds:Dataset)
OPTIONAL MATCH (p)-[:WRITES_DATASET]->(dsw:Dataset)
OPTIONAL MATCH (j:Job)-[:INVOKES]->(p)
OPTIONAL MATCH (p)-[:USES_COPYBOOK]->(c:Copybook)
OPTIONAL MATCH (p)-[:ACCESSES_DB2]->(db2:DB2Table)
RETURN p.program_id                    AS program_id,
       collect(DISTINCT ds.dd_name)    AS reads_datasets,
       collect(DISTINCT dsw.dd_name)   AS writes_datasets,
       collect(DISTINCT j.job_id)      AS invoked_by_jobs,
       collect(DISTINCT c.name)        AS copybooks,
       collect(DISTINCT db2.name)      AS db2_tables
"""

EXPAND_JCL_JOB = """
MATCH (d:RagDocument {id: $doc_id})-[:DOCUMENTS]->(j:Job)
OPTIONAL MATCH (j)-[:HAS_STEP]->(s:Step)-[:EXECUTES]->(p:Program)
OPTIONAL MATCH (j)-[:HAS_STEP]->(sr:Step)-[:READS]->(jdr:JclDataset)
OPTIONAL MATCH (j)-[:HAS_STEP]->(sw:Step)-[:WRITES]->(jdw:JclDataset)
RETURN j.job_id                                              AS job_id,
       collect(DISTINCT s.step_name + '->' + p.program_id)   AS step_programs,
       collect(DISTINCT jdr.dsn_name)                         AS reads_datasets,
       collect(DISTINCT jdw.dsn_name)                         AS writes_datasets
"""

EXPAND_JCL_DATASET = """
MATCH (d:RagDocument {id: $doc_id})-[:DOCUMENTS]->(ds)
WHERE ds:JclDataset OR ds:Dataset
OPTIONAL MATCH (s:Step)-[:READS]->(ds)
OPTIONAL MATCH (sw:Step)-[:WRITES]->(ds)
OPTIONAL MATCH (j:Job)-[:READS_DATASET]->(ds)
OPTIONAL MATCH (jw:Job)-[:WRITES_DATASET]->(ds)
RETURN coalesce(ds.dsn_name, ds.name, ds.dataset_id) AS dataset_name,
       collect(DISTINCT s.step_id)                     AS read_by_steps,
       collect(DISTINCT sw.step_id)                    AS written_by_steps,
       collect(DISTINCT j.job_id)                      AS read_by_jobs,
       collect(DISTINCT jw.job_id)                     AS written_by_jobs
"""

EXPAND_MALLA_OBJECT = """
MATCH (d:RagDocument {id: $doc_id})-[:DOCUMENTS]->(m:MallaObject)
OPTIONAL MATCH (m)-[:REQUIRES_CONDITION]->(cin:MallaCondition)
WITH m, collect(DISTINCT cin.name + ' (' + coalesce(cin.odat,'') + ')') AS input_conditions
OPTIONAL MATCH (m)-[:PRODUCES_CONDITION]->(cout:MallaCondition)
WITH m, input_conditions,
     collect(DISTINCT cout.name + ' (' + coalesce(cout.odat,'') + ')') AS output_conditions
OPTIONAL MATCH (m)-[dep:DEPENDS_ON]->(pred:MallaObject)
WITH m, input_conditions, output_conditions,
     collect(DISTINCT pred.memname + ' via ' + coalesce(dep.condition_name,'')) AS predecessors
OPTIONAL MATCH (m)-[pre:PRECEDES]->(succ:MallaObject)
WITH m, input_conditions, output_conditions, predecessors,
     collect(DISTINCT succ.memname + ' via ' + coalesce(pre.condition_name,'')) AS successors
OPTIONAL MATCH (m)-[:SCHEDULES_JOB]->(j:Job)
RETURN m.object_id AS object_id,
       m.malla_id AS malla_id,
       m.source_file AS source_file,
       m.memname AS memname,
       m.typ AS typ,
       m.group AS group_name,
       m.table AS table_name,
       m.description AS description,
       input_conditions,
       output_conditions,
       predecessors,
       successors,
       collect(DISTINCT j.job_id) AS linked_jobs
"""

EXPAND_ROUTINE = """
MATCH (d:RagDocument {id: $doc_id})-[:DOCUMENTS]->(r:Routine)
OPTIONAL MATCH (p:Program)-[:CALLS]->(r)
WITH r, collect(DISTINCT p.program_id) AS invoked_by_programs
OPTIONAL MATCH (r)-[:CALLS]->(called:Routine)
WITH r, invoked_by_programs, collect(DISTINCT called.routine_id) AS called_routines
OPTIONAL MATCH (r)-[dbrel:ACCESSES_DB2]->(t:DB2Table)
WITH r, invoked_by_programs, called_routines,
     collect(DISTINCT t.db2table_id + ' (' + coalesce(dbrel.operation,'') + ')') AS db2_tables
OPTIONAL MATCH (r)-[:HAS_VARIABLE]->(vin:RoutineVariable)
WITH r, invoked_by_programs, called_routines, db2_tables,
     collect(DISTINCT CASE WHEN vin.direction IN ['INPUT','INOUT'] THEN vin.name END) AS input_variables
OPTIONAL MATCH (r)-[:HAS_VARIABLE]->(vout:RoutineVariable)
WITH r, invoked_by_programs, called_routines, db2_tables, input_variables,
     collect(DISTINCT CASE WHEN vout.direction IN ['OUTPUT','INOUT'] THEN vout.name END) AS output_variables
RETURN r.routine_id AS routine_id,
       r.name AS name,
       r.loc AS loc,
       r.technical_debt_score AS debt_score,
       r.technical_debt_level AS debt_level,
       r.complexity_score AS complexity_score,
       r.complexity_level AS complexity_level,
       r.business_value_score AS business_value_score,
       r.business_value_level AS business_value_level,
       r.security_score AS security_score,
       r.security_level AS security_level,
       r.security_rationale AS security_rationale,
       r.processing_time_score AS processing_time_score,
       r.processing_time_level AS processing_time_level,
       invoked_by_programs,
       called_routines,
       db2_tables,
       input_variables,
       output_variables
"""

# Búsqueda directa por nombre de dataset (DD name o fragmento de DSN)
DIRECT_DATASET_SEARCH = """
UNWIND $keywords AS kw
MATCH (ds)
WHERE (ds:Dataset OR ds:JclDataset)
  AND ( toUpper(coalesce(ds.dd_name,''))      CONTAINS toUpper(kw)
     OR toUpper(coalesce(ds.dsn_name,''))     CONTAINS toUpper(kw)
     OR toUpper(coalesce(ds.dataset_id,''))   CONTAINS toUpper(kw) )
WITH DISTINCT ds
OPTIONAL MATCH (pr:Program)-[:READS_DATASET]->(ds)
OPTIONAL MATCH (pw:Program)-[:WRITES_DATASET]->(ds)
OPTIONAL MATCH (sr:Step)-[:READS]->(ds)
OPTIONAL MATCH (sw:Step)-[:WRITES]->(ds)
RETURN coalesce(ds.dd_name, ds.dsn_name, ds.dataset_id) AS dataset_name,
       collect(DISTINCT pr.program_id)                   AS read_by_programs,
       collect(DISTINCT pw.program_id)                   AS written_by_programs,
       collect(DISTINCT sr.step_name)                    AS read_by_steps,
       collect(DISTINCT sw.step_name)                    AS written_by_steps
"""

DIRECT_ROUTINE_SEARCH = """
UNWIND $keywords AS kw
MATCH (r:Routine)
WHERE toUpper(coalesce(r.routine_id,'')) = toUpper(kw)
   OR toUpper(coalesce(r.name,'')) = toUpper(kw)
   OR toUpper(coalesce(r.routine_id,'')) CONTAINS toUpper(kw)
   OR toUpper(coalesce(r.name,'')) CONTAINS toUpper(kw)
WITH DISTINCT r
OPTIONAL MATCH (p:Program)-[:CALLS]->(r)
WITH r, collect(DISTINCT p.program_id) AS invoked_by_programs
OPTIONAL MATCH (r)-[:CALLS]->(called:Routine)
WITH r, invoked_by_programs, collect(DISTINCT called.routine_id) AS called_routines
OPTIONAL MATCH (r)-[dbrel:ACCESSES_DB2]->(t:DB2Table)
WITH r, invoked_by_programs, called_routines,
     collect(DISTINCT t.db2table_id + ' (' + coalesce(dbrel.operation,'') + ')') AS db2_tables
OPTIONAL MATCH (r)-[:HAS_VARIABLE]->(vin:RoutineVariable)
WITH r, invoked_by_programs, called_routines, db2_tables,
     collect(DISTINCT CASE WHEN vin.direction IN ['INPUT','INOUT'] THEN vin.name END) AS input_variables
OPTIONAL MATCH (r)-[:HAS_VARIABLE]->(vout:RoutineVariable)
WITH r, invoked_by_programs, called_routines, db2_tables, input_variables,
     collect(DISTINCT CASE WHEN vout.direction IN ['OUTPUT','INOUT'] THEN vout.name END) AS output_variables
RETURN r.routine_id AS routine_id,
       r.name AS name,
       r.loc AS loc,
       r.technical_debt_score AS debt_score,
       r.technical_debt_level AS debt_level,
       r.complexity_score AS complexity_score,
       r.complexity_level AS complexity_level,
       r.business_value_score AS business_value_score,
       r.business_value_level AS business_value_level,
       r.security_score AS security_score,
       r.security_level AS security_level,
       r.security_rationale AS security_rationale,
       r.processing_time_score AS processing_time_score,
       r.processing_time_level AS processing_time_level,
       invoked_by_programs,
       called_routines,
       db2_tables,
       input_variables,
       output_variables
ORDER BY r.routine_id
LIMIT 20
"""

DIRECT_ROUTINE_SECURITY_DB2_IMPACT_SEARCH = """
MATCH (p:Program)-[:CALLS]->(r:Routine)-[dbrel:ACCESSES_DB2]->(t:DB2Table)
WHERE (toUpper(coalesce(r.security_level,'')) = 'ALTO' OR coalesce(r.security_score, 0) >= 75)
  AND (
        toUpper(coalesce(p.business_value_level,'')) IN ['ALTO','MEDIO']
        OR coalesce(p.business_value_score, 0) >= 30
      )
WITH r,
     collect(DISTINCT p.program_id + ' (valor negocio ' + coalesce(toString(p.business_value_score),'?') + '/100 ' + coalesce(p.business_value_level,'') + ')') AS programs,
     collect(DISTINCT t.db2table_id + ' (' + coalesce(dbrel.operation,'') + ')') AS db2_tables
RETURN r.routine_id AS routine_id,
       r.security_score AS security_score,
       r.security_level AS security_level,
       r.security_rationale AS security_rationale,
       r.business_value_score AS business_value_score,
       r.business_value_level AS business_value_level,
       r.complexity_score AS complexity_score,
       r.complexity_level AS complexity_level,
       programs,
       db2_tables
ORDER BY r.security_score DESC, r.business_value_score DESC, r.routine_id
LIMIT 20
"""

DIRECT_MALLA_OBJECT_SEARCH = """
UNWIND $keywords AS kw
MATCH (m:MallaObject)
WHERE toUpper(coalesce(m.memname,'')) = toUpper(kw)
   OR toUpper(coalesce(m.memname,'')) CONTAINS toUpper(kw)
   OR toUpper(coalesce(m.malla_id,'')) CONTAINS toUpper(kw)
   OR toUpper(coalesce(m.table,'')) CONTAINS toUpper(kw)
   OR toUpper(coalesce(m.group,'')) CONTAINS toUpper(kw)
   OR toUpper(coalesce(m.object_id,'')) CONTAINS toUpper(kw)
WITH DISTINCT m
OPTIONAL MATCH (m)-[:REQUIRES_CONDITION]->(cin:MallaCondition)
WITH m, collect(DISTINCT cin.name + ' (' + coalesce(cin.odat,'') + ')') AS input_conditions
OPTIONAL MATCH (m)-[:PRODUCES_CONDITION]->(cout:MallaCondition)
WITH m, input_conditions,
     collect(DISTINCT cout.name + ' (' + coalesce(cout.odat,'') + ')') AS output_conditions
OPTIONAL MATCH (m)-[dep:DEPENDS_ON]->(pred:MallaObject)
WITH m, input_conditions, output_conditions,
     collect(DISTINCT pred.memname + ' via ' + coalesce(dep.condition_name,'')) AS predecessors
OPTIONAL MATCH (m)-[pre:PRECEDES]->(succ:MallaObject)
WITH m, input_conditions, output_conditions, predecessors,
     collect(DISTINCT succ.memname + ' via ' + coalesce(pre.condition_name,'')) AS successors
OPTIONAL MATCH (m)-[:SCHEDULES_JOB]->(j:Job)
RETURN m.object_id AS object_id,
       m.malla_id AS malla_id,
       m.source_file AS source_file,
       m.memname AS memname,
       m.typ AS typ,
       m.group AS group_name,
       m.table AS table_name,
       m.description AS description,
       m.owner AS owner,
       m.application AS application,
       input_conditions,
       output_conditions,
       predecessors,
       successors,
       collect(DISTINCT j.job_id) AS linked_jobs
ORDER BY m.malla_id, m.memname
LIMIT 30
"""

DIRECT_MALLA_CONDITION_SEARCH = """
UNWIND $keywords AS kw
MATCH (c:MallaCondition)
WHERE toUpper(coalesce(c.name,'')) = toUpper(kw)
   OR toUpper(coalesce(c.name,'')) CONTAINS toUpper(kw)
   OR toUpper(coalesce(c.condition_id,'')) CONTAINS toUpper(kw)
WITH DISTINCT c
OPTIONAL MATCH (producer:MallaObject)-[:PRODUCES_CONDITION]->(c)
WITH c, collect(DISTINCT producer.memname + ' [' + producer.malla_id + ']') AS producers
OPTIONAL MATCH (consumer:MallaObject)-[:REQUIRES_CONDITION]->(c)
RETURN c.condition_id AS condition_id,
       c.name AS condition_name,
       c.odat AS odat,
       c.malla_id AS malla_id,
       c.source_file AS source_file,
       producers,
       collect(DISTINCT consumer.memname + ' [' + consumer.malla_id + ']') AS consumers
ORDER BY c.malla_id, c.name
LIMIT 30
"""

JOB_DEPENDENCY_CHAIN_SEARCH = """
UNWIND $keywords AS kw
OPTIONAL MATCH (m:MallaObject)
WHERE toUpper(coalesce(m.memname,'')) = toUpper(kw)
OPTIONAL MATCH (m)-[:SCHEDULES_JOB]->(mj:Job)
OPTIONAL MATCH (jById:Job)
WHERE toUpper(coalesce(jById.job_id,'')) = toUpper(kw)
WITH kw, m, coalesce(mj, jById) AS j
WHERE m IS NOT NULL OR j IS NOT NULL
OPTIONAL MATCH (m)-[:REQUIRES_CONDITION]->(cin:MallaCondition)
WITH kw, m, j,
     collect(DISTINCT cin.name + ' (' + coalesce(cin.odat,'') + ')') AS input_conditions
OPTIONAL MATCH (m)-[:PRODUCES_CONDITION]->(cout:MallaCondition)
WITH kw, m, j, input_conditions,
     collect(DISTINCT cout.name + ' (' + coalesce(cout.odat,'') + ')') AS output_conditions
OPTIONAL MATCH (m)-[dep:DEPENDS_ON]->(pred:MallaObject)
WITH kw, m, j, input_conditions, output_conditions,
     collect(DISTINCT pred.memname + ' via ' + coalesce(dep.condition_name,'')) AS predecessors
OPTIONAL MATCH (m)-[pre:PRECEDES]->(succ:MallaObject)
WITH kw, m, j, input_conditions, output_conditions, predecessors,
     collect(DISTINCT succ.memname + ' via ' + coalesce(pre.condition_name,'')) AS successors
OPTIONAL MATCH (j)-[:HAS_STEP]->(s:Step)-[:EXECUTES]->(p:Program)
WITH kw, m, j, input_conditions, output_conditions, predecessors, successors, s, p
ORDER BY s.sequence_number
OPTIONAL MATCH (p)-[:READS_DATASET]->(rds:Dataset)
WITH kw, m, j, input_conditions, output_conditions, predecessors, successors, s, p,
     collect(DISTINCT rds.dd_name) AS reads_datasets
OPTIONAL MATCH (p)-[:WRITES_DATASET]->(wds:Dataset)
WITH kw, m, j, input_conditions, output_conditions, predecessors, successors, s, p, reads_datasets,
     collect(DISTINCT wds.dd_name) AS writes_datasets
OPTIONAL MATCH (p)-[:ACCESSES_DB2]->(db2:DB2Table)
WITH kw, m, j, input_conditions, output_conditions, predecessors, successors, s, p, reads_datasets, writes_datasets,
     collect(DISTINCT db2.name) AS db2_tables
OPTIONAL MATCH (p)-[:CALLS]->(r:Routine)
RETURN kw AS requested_id,
       m.object_id AS object_id,
       m.malla_id AS malla_id,
       m.memname AS memname,
       m.typ AS typ,
       m.description AS description,
       input_conditions,
       output_conditions,
       predecessors,
       successors,
       j.job_id AS job_id,
       s.sequence_number AS step_order,
       s.step_name AS step_name,
       p.program_id AS program_id,
       reads_datasets,
       writes_datasets,
       db2_tables,
       collect(DISTINCT coalesce(r.routine_id, r.name)) AS routines,
       p.technical_debt_score AS debt_score,
       p.technical_debt_level AS debt_level,
       p.complexity_score AS complexity_score,
       p.complexity_level AS complexity_level,
       p.business_value_score AS business_value_score,
       p.business_value_level AS business_value_level,
       p.security_risk_score AS security_score,
       p.security_risk_level AS security_level,
       p.processing_time_sec AS processing_time_sec
ORDER BY m.malla_id, j.job_id, step_order, step_name, program_id
LIMIT 80
"""

MALLA_SECURITY_DATA_JOBS_SEARCH = """
UNWIND $keywords AS kw
MATCH (m:MallaObject)
WHERE toUpper(coalesce(m.malla_id,'')) = toUpper(kw)
   OR toUpper(coalesce(m.table,'')) = toUpper(kw)
   OR toUpper(coalesce(m.group,'')) = toUpper(kw)
WITH DISTINCT m
OPTIONAL MATCH (m)-[:SCHEDULES_JOB]->(j1:Job)
OPTIONAL MATCH (j2:Job {job_id: m.memname})
WITH m, collect(DISTINCT j1) + collect(DISTINCT j2) AS jobs
UNWIND jobs AS j
WITH DISTINCT m, j
WHERE j IS NOT NULL
OPTIONAL MATCH (j)-[:HAS_STEP]->(s:Step)-[:EXECUTES]->(p:Program)
WHERE p IS NOT NULL
OPTIONAL MATCH (p)-[:ACCESSES_DB2]->(db2:DB2Table)
WITH m, j, p,
     collect(DISTINCT db2.name) AS db2_tables
WHERE (
        $security_levels IS NULL
        OR size($security_levels) = 0
        OR toUpper(coalesce(p.security_risk_level,'')) IN $security_levels
        OR ($min_security_score IS NOT NULL AND coalesce(p.security_risk_score, 0) >= $min_security_score)
      )
  AND (
        $require_data_evidence = false
        OR $business_value_levels IS NULL
        OR size($business_value_levels) = 0
        OR size(db2_tables) > 0
        OR toUpper(coalesce(p.business_value_level,'')) IN $business_value_levels
        OR ($min_business_value_score IS NOT NULL AND coalesce(p.business_value_score, 0) >= $min_business_value_score)
      )
RETURN m.malla_id AS malla_id,
       j.job_id AS job_id,
       p.program_id AS program_id,
       p.security_risk_score AS security_score,
       p.security_risk_level AS security_level,
       p.business_value_score AS business_value_score,
       p.business_value_level AS business_value_level,
       db2_tables
ORDER BY security_score DESC, business_value_score DESC, malla_id, job_id, program_id
LIMIT $limit
"""

PROGRAM_AXES_QUERY = """
UNWIND $program_ids AS pid
MATCH (p:Program {program_id: pid})
RETURN p.program_id                  AS program_id,
       p.technical_debt_score        AS debt_score,
       p.technical_debt_level        AS debt_level,
       p.complexity_score            AS complexity_score,
       p.complexity_level            AS complexity_level,
       p.business_value_score        AS bv_score,
       p.business_value_level        AS bv_level,
       p.business_value_rationale    AS bv_rationale,
       p.security_risk_score         AS sec_score,
       p.security_risk_level         AS sec_level,
       p.security_risk_rationale     AS sec_rationale,
       p.processing_time_sec         AS proc_time,
       p.processing_time_rationale   AS time_rationale,
       p.technical_debt_rationale    AS debt_rationale,
       p.complexity_rationale        AS comp_rationale
"""

ROUTINE_AXES_QUERY = """
UNWIND $routine_ids AS rid
MATCH (r:Routine {routine_id: rid})
RETURN r.routine_id                  AS routine_id,
       r.technical_debt_score        AS debt_score,
       r.technical_debt_level        AS debt_level,
       r.technical_debt_rationale    AS debt_rationale,
       r.complexity_score            AS complexity_score,
       r.complexity_level            AS complexity_level,
       r.complexity_rationale        AS comp_rationale,
       r.business_value_score        AS bv_score,
       r.business_value_level        AS bv_level,
       r.business_value_rationale    AS bv_rationale,
       r.security_score              AS sec_score,
       r.security_level              AS sec_level,
       r.security_rationale          AS sec_rationale,
       r.processing_time_score       AS proc_time,
       r.processing_time_level       AS proc_time_level,
       r.processing_time_rationale   AS time_rationale
"""


def _extract_entity_ids_from_text(text: str) -> list:
    """Extrae IDs mainframe mencionados en una respuesta para enfocar ejes/grafo."""
    if not text:
        return []
    ids = re.findall(r'\b[A-Za-z]{2,}[0-9][A-Za-z0-9#$@-]{2,}\b', text)
    seen = set()
    ordered = []
    for entity_id in ids:
        entity_id = entity_id.upper()
        if entity_id not in seen:
            seen.add(entity_id)
            ordered.append(entity_id)
    return ordered


def _program_axes_record(row: dict) -> dict:
    return {
        'debt':          int(row.get('debt_score') or 0),
        'debt_lv':       row.get('debt_level') or '',
        'complexity':    int(row.get('complexity_score') or 0),
        'comp_lv':       row.get('complexity_level') or '',
        'bv':            int(row.get('bv_score') or 0),
        'bv_lv':         row.get('bv_level') or '',
        'bv_rationale':  row.get('bv_rationale') or '',
        'security':      int(row.get('sec_score') or 0),
        'sec_lv':        row.get('sec_level') or '',
        'sec_rationale': row.get('sec_rationale') or '',
        'proc_time':     int(row.get('proc_time') or 0),
        'time_rationale':row.get('time_rationale') or '',
        'debt_rationale':row.get('debt_rationale') or '',
        'comp_rationale':row.get('comp_rationale') or '',
    }


def _routine_axes_record(row: dict) -> dict:
    return {
        'entity_type':   'routine',
        'debt':          int(row.get('debt_score') or 0),
        'debt_lv':       row.get('debt_level') or '',
        'complexity':    int(row.get('complexity_score') or 0),
        'comp_lv':       row.get('complexity_level') or '',
        'bv':            int(row.get('bv_score') or 0),
        'bv_lv':         row.get('bv_level') or '',
        'bv_rationale':  row.get('bv_rationale') or '',
        'security':      int(row.get('sec_score') or 0),
        'sec_lv':        row.get('sec_level') or '',
        'sec_rationale': row.get('sec_rationale') or '',
        'proc_time':     int(row.get('proc_time') or 0),
        'time_lv':       row.get('proc_time_level') or '',
        'time_rationale':row.get('time_rationale') or '',
        'debt_rationale':row.get('debt_rationale') or '',
        'comp_rationale':row.get('comp_rationale') or '',
    }


def fetch_axes_for_entities(entity_ids: list) -> dict:
    """Busca ejes de programas y rutinas para los IDs que aparecen en la respuesta final."""
    if not entity_ids:
        return {}
    axes_data = {}
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            for row in session.run(PROGRAM_AXES_QUERY, program_ids=entity_ids).data():
                axes_data[row['program_id']] = _program_axes_record(row)
            for row in session.run(ROUTINE_AXES_QUERY, routine_ids=entity_ids).data():
                axes_data[row['routine_id']] = _routine_axes_record(row)
    finally:
        driver.close()
    return axes_data

# ══════════════════════════════════════════════════════════════════════════════
# LOGICA DE BUSQUEDA Y EXPANSION EN EL GRAFO
# ══════════════════════════════════════════════════════════════════════════════
# Palabras vacias que no aportan a la busqueda
_STOPWORDS = {
    "quiero","ver","los","las","que","tiene","del","de","la","el",
    "un","una","en","con","por","para","como","cual","cuales","hay",
    "tiene","tienen","hace","hacen","dame","muestra","mostrar","listar",
    "pasos","paso","steps","step","programa","programas","job","jobs",
    "dataset","datasets","informacion","sobre","this","the","what",
    # verbos españoles genéricos que no aportan a la búsqueda técnica
    "realizan","realiza","realizan","usan","usan","utilizan","ejecutan",
    "acceden","tienen","contienen","incluyen","generan","producen",
    "cual","cuales","cuales","cuando","donde","como","seran","seria",
    "seria","pueden","podrian","deberian","necesitan","deben",
}

# Palabras que indican intención de listado general
_LIST_INTENT = {
    "existen","existe","hay","cuantos","cuántos","cuantas","cuántas",
    "todos","todas","lista","listar","dame","muestra","mostrar","enumera",
    "cuales","cuáles","available","list","all","show",
}

# Queries de listado general
LIST_ALL_JOBS = """
MATCH (j:Job)
RETURN j.job_id AS job_id
ORDER BY j.job_id
"""

LIST_ALL_PROGRAMS = """
MATCH (p:Program)
RETURN p.program_id AS program_id
ORDER BY p.program_id
"""

LIST_ALL_DATASETS = """
MATCH (d:Dataset)
RETURN d.dd_name AS dd_name
ORDER BY d.dd_name
"""

LIST_ALL_MALLAS = """
MATCH (m:MallaObject)
RETURN m.malla_id AS malla_id,
       count(DISTINCT m) AS object_count,
       count(DISTINCT m.memname) AS job_count
ORDER BY m.malla_id
"""

TOP_PROGRAM_PROCESSING_TIME = """
MATCH (p:Program)
WHERE p.processing_time_sec IS NOT NULL
RETURN p.program_id AS id,
       'programa' AS entity_type,
       p.processing_time_sec AS score,
       p.processing_time_rationale AS rationale
ORDER BY score DESC, id
LIMIT $limit
"""

TOP_ROUTINE_PROCESSING_TIME = """
MATCH (r:Routine)
WHERE r.processing_time_score IS NOT NULL
RETURN r.routine_id AS id,
       'rutina' AS entity_type,
       r.processing_time_score AS score,
       r.processing_time_level AS level,
       r.processing_time_rationale AS rationale
ORDER BY score DESC, id
LIMIT $limit
"""

FILTERED_PROGRAM_PROCESSING_TIME = """
MATCH (p:Program)
WHERE p.processing_time_sec IS NOT NULL
  AND ($min_time IS NULL OR p.processing_time_sec >= $min_time)
  AND ($debt_levels IS NULL OR size($debt_levels) = 0 OR toUpper(p.technical_debt_level) IN $debt_levels)
  AND ($complexity_levels IS NULL OR size($complexity_levels) = 0 OR toUpper(p.complexity_level) IN $complexity_levels)
  AND ($business_value_levels IS NULL OR size($business_value_levels) = 0 OR toUpper(p.business_value_level) IN $business_value_levels)
  AND ($security_levels IS NULL OR size($security_levels) = 0 OR toUpper(p.security_risk_level) IN $security_levels)
RETURN p.program_id AS id,
       'programa' AS entity_type,
       p.processing_time_sec AS score,
       p.processing_time_rationale AS rationale,
       p.technical_debt_level AS debt_level,
       p.complexity_level AS complexity_level,
       p.business_value_level AS business_value_level,
       p.security_risk_level AS security_level
ORDER BY score DESC, id
LIMIT $limit
"""


def _requested_limit(question: str, default: int = 5, max_limit: int = 20) -> int:
    m = re.search(r'\b(\d{1,2})\b', question)
    if m:
        return max(1, min(max_limit, int(m.group(1))))
    words = {
        "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
        "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    }
    q = question.lower()
    for word, value in words.items():
        if re.search(rf'\b{word}\b', q):
            return value
    return default


def _processing_time_ranking_intent(question: str) -> bool:
    q = question.lower()
    has_time_axis = any(term in q for term in (
        "tiempo de proceso", "tiempo proceso", "tiempo de procesamiento",
        "procesamiento", "proceso muy elevado",
    ))
    has_ranking = any(term in q for term in (
        "elevado", "elevados", "alto", "altos", "mayor", "mayores",
        "top", "ranking", "lista", "dame", "presenta", "presentan",
    ))
    return has_time_axis and has_ranking


def _axis_level_filters(question: str) -> dict:
    q = question.lower()

    def level_near(axis_patterns: list):
        for axis in axis_patterns:
            low_before = rf'\b(baja|bajo|bajas|bajos|menor|menores|mínima|mínimo|minima|minimo)\s+{axis}\b'
            low_after = rf'\b{axis}\s+(baja|bajo|bajas|bajos|menor|menores|mínima|mínimo|minima|minimo)\b'
            high_before = rf'\b(alta|alto|altas|altos|elevada|elevado|elevadas|elevados|mayor|mayores|máxima|máximo|maxima|maximo)\s+{axis}\b'
            high_after = rf'\b{axis}\s+(alta|alto|altas|altos|elevada|elevado|elevadas|elevados|mayor|mayores|máxima|máximo|maxima|maximo)\b'
            medium_before = rf'\b(media|medio|medias|medios)\s+{axis}\b'
            medium_after = rf'\b{axis}\s+(media|medio|medias|medios)\b'
            if re.search(low_before, q) or re.search(low_after, q):
                return "BAJO"
            if re.search(high_before, q) or re.search(high_after, q):
                return "ALTO"
            if re.search(medium_before, q) or re.search(medium_after, q):
                return "MEDIO"
        return None

    filters = {}
    debt = level_near([r"deuda(?:\s+t[eé]cnica)?"])
    complexity = level_near([r"complejidad"])
    business_value = level_near([r"valor(?:\s+de)?\s+negocio"])
    security = level_near([r"(?:riesgo\s+)?seguridad", r"riesgo(?:\s+de)?\s+seguridad"])
    processing_time = level_near([r"tiempo(?:\s+de)?\s+proceso", r"tiempo(?:\s+de)?\s+procesamiento"])

    if debt:
        filters["debt_level"] = debt
    if complexity:
        filters["complexity_level"] = complexity
    if business_value:
        filters["business_value_level"] = business_value
    if security:
        filters["security_level"] = security
    if processing_time:
        filters["processing_time_level"] = processing_time
    return filters


def _level_relaxation(level) -> list:
    if level == "BAJO":
        return [["BAJO"], ["BAJO", "MEDIO"], ["BAJO", "MEDIO", "ALTO"]]
    if level == "ALTO":
        return [["ALTO"], ["ALTO", "MEDIO"], ["ALTO", "MEDIO", "BAJO"]]
    if level == "MEDIO":
        return [["MEDIO"], ["BAJO", "MEDIO", "ALTO"]]
    return [None]


def _time_relaxation(level) -> list:
    if level == "ALTO":
        return [(600, "ALTO"), (300, "ALTO o MEDIO"), (None, "cualquier nivel")]
    if level == "MEDIO":
        return [(300, "MEDIO o superior"), (None, "cualquier nivel")]
    return [(None, "cualquier nivel")]


def _relaxed_axis_attempts(filters: dict) -> list:
    sequences = {
        "processing_time": _time_relaxation(filters.get("processing_time_level")),
        "debt": _level_relaxation(filters.get("debt_level")),
        "complexity": _level_relaxation(filters.get("complexity_level")),
        "business_value": _level_relaxation(filters.get("business_value_level")),
        "security": _level_relaxation(filters.get("security_level")),
    }
    max_len = max(len(v) for v in sequences.values())
    attempts = []
    for idx in range(max_len):
        time_min, time_label = sequences["processing_time"][min(idx, len(sequences["processing_time"]) - 1)]
        attempts.append({
            "relaxation_step": idx,
            "min_time": time_min,
            "time_label": time_label,
            "debt_levels": sequences["debt"][min(idx, len(sequences["debt"]) - 1)],
            "complexity_levels": sequences["complexity"][min(idx, len(sequences["complexity"]) - 1)],
            "business_value_levels": sequences["business_value"][min(idx, len(sequences["business_value"]) - 1)],
            "security_levels": sequences["security"][min(idx, len(sequences["security"]) - 1)],
        })
    return attempts


def _detect_list_intent(question: str) -> str:
    """
    Detecta si la pregunta es una solicitud de listado general.
    Retorna 'jobs', 'programs', 'datasets', 'mallas' o '' si no aplica.
    Si la pregunta menciona un identificador concreto (ej: MPJP001J, BG4CIOB5)
    NO se considera listado general aunque contenga palabras como "cuántos".
    """
    import re
    q = question.lower()
    words = set(re.findall(r'[a-záéíóúüñ]+', q))
    has_list = bool(words & _LIST_INTENT)
    if not has_list:
        return ''
    # Si hay un token que parece identificador concreto (≥6 chars, mezcla letras+dígitos
    # o todo mayúsculas con dígitos) → es una pregunta sobre un elemento específico,
    # no un listado general.
    _id_like = re.findall(r'\b[A-Za-z]{2,}[0-9][A-Za-z0-9#$@-]{2,}\b', question)
    if _id_like:
        return ''
    if any(w in q for w in ("job", "jobs", "jcl", "trabajo", "trabajos")):
        return 'jobs'
    if any(w in q for w in ("programa", "programas", "program", "programs", "cobol")):
        return 'programs'
    if any(w in q for w in ("dataset", "datasets", "fichero", "ficheros", "archivo", "archivos")):
        return 'datasets'
    if any(w in q for w in ("malla", "mallas", "control-m", "controlm", "schedule", "schedules")):
        return 'mallas'
    return ''

def extract_keywords(question: str) -> list:
    """
    Extrae palabras clave de la pregunta filtrando stopwords.
    Siempre incluye la pregunta completa como primer keyword
    por si es un nombre exacto (ej: 'MPJP7599').
    """
    import re
    # Términos cortos pero semánticamente importantes (excepción al filtro len>=4)
    _SHORT_IMPORTANT = {"db2", "sql", "jcl", "ims", "cics", "vsam", "mvs", "db", "mq"}
    words = re.findall(r'[A-Za-z0-9_#@$]+', question)
    keywords = []
    # Primero las palabras largas o que parecen nombres de sistema (mayusculas/numeros)
    for w in words:
        wl = w.lower()
        if wl in _SHORT_IMPORTANT or (len(w) >= 4 and wl not in _STOPWORDS):
            keywords.append(w)
    # Si no hay keywords utiles, usar todas las palabras de 3+ chars
    if not keywords:
        keywords = [w for w in words if len(w) >= 3]
    return keywords if keywords else [question]


def _clean(lst):
    """Filtra None y vacíos de una lista."""
    return [x for x in (lst or []) if x]

def _has_malla_intent(question: str) -> bool:
    q = question.lower()
    return any(term in q for term in (
        "malla", "mallas", "control-m", "controlm", "condicion", "condición",
        "condiciones", "predecesor", "predecesores", "sucesor", "sucesores",
        "depende", "dependen", "dependencia", "dependencias", "habilita",
        "habilitan", "entrada", "salida",
    ))

def _has_routine_intent(question: str) -> bool:
    q = question.lower()
    return any(term in q for term in ("rutina", "rutinas", "subprograma", "subprogramas"))

def _routine_security_db2_impact_intent(question: str) -> bool:
    q = question.lower()
    return (
        _has_routine_intent(question)
        and "db2" in q
        and ("seguridad" in q or "riesgo" in q)
        and ("impacto" in q or "valor" in q or "critico" in q or "crítico" in q)
    )

def _dependency_chain_intent(question: str) -> bool:
    q = question.lower()
    return any(term in q for term in (
        "cadena", "lineage", "trazabilidad", "dependencia completa",
        "flujo completo", "desde la malla", "hasta las rutinas",
    ))

def _malla_security_data_jobs_intent(question: str) -> bool:
    q = question.lower()
    return (
        ("malla" in q or "mallas" in q)
        and ("job" in q or "jobs" in q)
        and ("programa" in q or "programas" in q)
        and ("seguridad" in q or "riesgo" in q)
        and ("dato" in q or "datos" in q or "db2" in q)
    )

def _malla_security_data_attempts() -> list:
    return [
        {
            "label": "seguridad ALTO/MUY_ALTO y datos ALTO/MUY_ALTO o DB2",
            "security_levels": ["ALTO", "MUY_ALTO"],
            "min_security_score": 60,
            "business_value_levels": ["ALTO", "MUY_ALTO"],
            "min_business_value_score": 70,
            "require_data_evidence": True,
        },
        {
            "label": "seguridad ALTO/MEDIO y datos ALTO/MEDIO o DB2",
            "security_levels": ["MEDIO", "ALTO", "MUY_ALTO"],
            "min_security_score": 30,
            "business_value_levels": ["MEDIO", "ALTO", "MUY_ALTO"],
            "min_business_value_score": 30,
            "require_data_evidence": True,
        },
        {
            "label": "cualquier riesgo de seguridad, priorizando mayor score y evidencia de datos",
            "security_levels": [],
            "min_security_score": None,
            "business_value_levels": [],
            "min_business_value_score": None,
            "require_data_evidence": False,
        },
    ]

def _malla_focus(question: str) -> str:
    q = question.lower()
    if _dependency_chain_intent(question):
        return ""
    if any(term in q for term in (
        "que habilitan a", "qué habilitan a", "jobs que habilitan a",
        "job que habilita a", "habilitan a", "habilita a",
        "predecesor", "predecesores", "depende", "dependen",
    )):
        return "predecessors"
    if any(term in q for term in (
        "que jobs habilita", "qué jobs habilita", "que job habilita",
        "qué job habilita", "jobs habilitados por", "job habilitado por",
        "sucesor", "sucesores",
    )):
        return "successors"
    if any(term in q for term in ("habilita", "habilitan", "sucesor", "sucesores")):
        return "successors"
    if "entrada" in q or "requiere" in q or "necesita" in q:
        return "inputs"
    if "salida" in q or "deja" in q or "produce" in q:
        return "outputs"
    return ""


def _malla_edge_names(values: list) -> list:
    """Extrae solo el nombre del job/objeto desde textos como 'JOBX via COND-OK'."""
    names = []
    seen = set()
    for value in values or []:
        name = str(value).split(" via ", 1)[0].strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


# ══════════════════════════════════════════════════════════════════════════════
# VISUALIZACION DE GRAFO (vis.js via CDN)
# ══════════════════════════════════════════════════════════════════════════════
_VIS_CDN = "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"

_APP_CSS = """
/* ─── GraphRAG Professional Theme ──────────────────────────── */
body, .gradio-container {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}
.gradio-container { max-width: 1300px !important; margin: 0 auto !important; }
footer, .built-with { display: none !important; }
#btn-send {
    background: linear-gradient(135deg, #1e40af, #3b82f6) !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
    letter-spacing: 0.02em !important;
    box-shadow: 0 2px 12px rgba(59,130,246,.35) !important;
    transition: box-shadow .15s, transform .15s !important;
}
#btn-send:hover {
    box-shadow: 0 4px 20px rgba(59,130,246,.55) !important;
    transform: translateY(-1px) !important;
}
#btn-clear { border-radius: 8px !important; }
#chatbot  { border-radius: 12px !important; }
"""


def parse_answer_to_graph(answer: str) -> dict:
    """
    Parsea el texto de direct_answer para extraer nodos y aristas
    que representan las dependencias encontradas en el grafo.
    """
    import re as _re
    nodes    = {}       # id -> {id, label, group}
    edges    = []
    edge_set = set()
    current  = None     # (id, group) del nodo central activo

    def add_node(nid, group):
        nid = nid.strip()
        if nid and nid not in nodes:
            nodes[nid] = {"id": nid, "label": nid, "group": group}

    def add_edge(src, dst, lbl):
        src, dst = src.strip(), dst.strip()
        key = (src, dst, lbl)
        if src and dst and key not in edge_set:
            edge_set.add(key)
            edges.append({"from": src, "to": dst, "label": lbl})

    # Respuestas de listado general — demasiados nodos, no graficar
    if _re.match(r"(Jobs|Programas|Datasets) en el sistema", answer):
        return {"nodes": [], "edges": []}

    for raw in answer.split("\n"):
        line = raw.strip()

        m = _re.match(r"^Programa:\s+(\S+)", line)
        if m:
            add_node(m.group(1), "program")
            current = (m.group(1), "program"); continue

        m = _re.match(r"^JOB:\s+(\S+)", line)
        if m:
            add_node(m.group(1), "job")
            current = (m.group(1), "job"); continue

        m = _re.match(r"^Dataset:\s+(\S+)", line)
        if m:
            add_node(m.group(1), "dataset")
            current = (m.group(1), "dataset"); continue

        m = _re.match(r"^Step:\s+(\S+)", line)
        if m:
            add_node(m.group(1), "step")
            current = (m.group(1), "step"); continue

        m = _re.match(r"^Rutina:\s+(\S+)", line)
        if m:
            add_node(m.group(1), "routine")
            current = (m.group(1), "routine"); continue

        m = _re.match(r"^-\s+(\S+):.*?programas:\s*(.*?);\s*DB2:\s*(.*?)(?:;\s*motivo|$)", line)
        if m:
            rid = m.group(1).strip()
            add_node(rid, "routine")
            current = (rid, "routine")
            for p in [x.strip() for x in m.group(2).split(",") if x.strip()]:
                pname = _re.sub(r"\s*\([^)]*\)", "", p).strip()
                if pname:
                    add_node(pname, "program"); add_edge(pname, rid, "INVOCA")
            for d in [x.strip() for x in m.group(3).split(",") if x.strip()]:
                dname = _re.sub(r"\s*\([^)]*\)", "", d).strip()
                if dname:
                    add_node(dname, "db2"); add_edge(rid, dname, "DB2")
            continue

        if not current:
            continue
        cid, cgrp = current

        m = _re.match(r"Invocado por JOBs:\s+(.+)", line)
        if m:
            for j in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                add_node(j, "job"); add_edge(j, cid, "INVOCA")
            continue

        m = _re.match(r"Invocada por programas:\s+(.+)", line)
        if m:
            for p in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                add_node(p, "program"); add_edge(p, cid, "INVOCA")
            continue

        m = _re.match(r"Invoca rutinas:\s+(.+)", line)
        if m:
            for r in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                add_node(r, "routine"); add_edge(cid, r, "LLAMA")
            continue

        m = _re.match(r"Lee datasets:\s+(.+)", line)
        if m:
            for d in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                add_node(d, "dataset"); add_edge(cid, d, "LEE")
            continue

        m = _re.match(r"Escribe datasets:\s+(.+)", line)
        if m:
            for d in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                add_node(d, "dataset"); add_edge(cid, d, "ESCRIBE")
            continue

        m = _re.match(r"Copybooks:\s+(.+)", line)
        if m:
            for c in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                add_node(c, "copybook"); add_edge(cid, c, "COPY")
            continue

        m = _re.match(r"Tablas DB2[^:]*:\s+(.+)", line)
        if m:
            for d in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                dn = _re.sub(r"\s*\([^)]*\)", "", d).strip()
                if dn:
                    add_node(dn, "db2"); add_edge(cid, dn, "DB2")
            continue

        m = _re.match(r"Variables entrada:\s+(.+)", line)
        if m:
            for v in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                add_node(v, "variable"); add_edge(v, cid, "INPUT")
            continue

        m = _re.match(r"Variables salida:\s+(.+)", line)
        if m:
            for v in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                add_node(v, "variable"); add_edge(cid, v, "OUTPUT")
            continue

        m = _re.match(r"Rutinas est.ticas:\s+(.+)", line)
        if m:
            for r in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                rn = _re.sub(r"\s*\(x\d+\)$", "", r).strip()
                if rn: add_node(rn, "routine"); add_edge(cid, rn, "LLAMA(S)")
            continue

        m = _re.match(r"Rutinas din.micas:\s+(.+)", line)
        if m:
            for r in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                rn = _re.sub(r"\s*\(x\d+\)$", "", r).strip()
                if rn: add_node(rn, "routine"); add_edge(cid, rn, "LLAMA(D)")
            continue

        m = _re.match(r"Layouts embebidos[^:]*:\s+(.+)", line)
        if m:
            for lay in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                ln = _re.sub(r"\s*\([^)]+\)$", "", lay).strip()
                if ln: add_node(ln, "layout"); add_edge(cid, ln, "LAYOUT")
            continue

        # Steps en orden: "  10. STEPNAME -> PROG"
        m = _re.match(r"(\d+)\.\s+(\S+)\s*->\s*(\S+)", line)
        if m:
            sname, pname = m.group(2), m.group(3)
            add_node(sname, "step"); add_edge(cid, sname, m.group(1)+".")
            if pname and pname != "(sin":
                add_node(pname, "program"); add_edge(sname, pname, "EXEC")
            continue

        m = _re.match(r"Le[\u00ed]do por programas:\s+(.+)", line)
        if m:
            for p in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                add_node(p, "program"); add_edge(p, cid, "LEE")
            continue

        m = _re.match(r"Escrito por programas:\s+(.+)", line)
        if m:
            for p in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                add_node(p, "program"); add_edge(p, cid, "ESCRIBE")
            continue

    # Limitar a 60 nodos para no saturar el grafo
    if len(nodes) > 60:
        nlist = dict(list(nodes.items())[:60])
        nids  = set(nlist.keys())
        return {"nodes": list(nlist.values()),
                "edges": [e for e in edges if e["from"] in nids and e["to"] in nids]}
    return {"nodes": list(nodes.values()), "edges": edges}


def build_graph_html(graph_data: dict) -> str:
    import json as _json
    if not graph_data or not graph_data.get("nodes"):
        return "<p style='color:#aaa;padding:10px;font-style:italic'>Sin dependencias para visualizar.</p>"

    nodes_j = _json.dumps(graph_data["nodes"])
    edges_j = _json.dumps(graph_data["edges"])

    # Usamos <iframe srcdoc> para que los scripts se ejecuten correctamente
    # (Gradio bloquea scripts inline en gr.HTML)
    inner = f"""<!DOCTYPE html>
<html translate="no"><head>
<meta name="google" content="notranslate">
<script src="{_VIS_CDN}"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:linear-gradient(160deg,#0d1117 0%,#161b2c 100%);overflow:hidden}}
  #gr{{width:100%;height:400px}}
  #hint{{position:absolute;bottom:8px;left:12px;font-size:10px;
         color:rgba(255,255,255,0.28);pointer-events:none;font-family:Inter,sans-serif}}
</style>
</head><body translate="no">
<div id="gr"></div>
<div id="hint">🖱 Rueda: zoom &middot; Arrastrar: mover &middot; Doble clic: fijar</div>
<script>
var N = new vis.DataSet({nodes_j});
var E = new vis.DataSet({edges_j});
var opts = {{
  groups:{{
    program: {{color:{{background:'#3b82f6',border:'#1d4ed8',highlight:{{background:'#60a5fa',border:'#2563eb'}}}},font:{{color:'#fff',size:13,face:'Inter,sans-serif'}},shape:'box',borderWidth:2}},
    job:     {{color:{{background:'#f59e0b',border:'#b45309',highlight:{{background:'#fbbf24',border:'#d97706'}}}},font:{{color:'#1a1a1a',size:13,face:'Inter,sans-serif'}},shape:'ellipse',borderWidth:2}},
    dataset: {{color:{{background:'#22c55e',border:'#15803d',highlight:{{background:'#4ade80',border:'#16a34a'}}}},font:{{color:'#fff',size:12,face:'Inter,sans-serif'}},shape:'ellipse',borderWidth:2}},
    copybook:{{color:{{background:'#a855f7',border:'#7e22ce',highlight:{{background:'#c084fc',border:'#9333ea'}}}},font:{{color:'#fff',size:12,face:'Inter,sans-serif'}},shape:'box',borderWidth:2}},
    db2:     {{color:{{background:'#ef4444',border:'#b91c1c',highlight:{{background:'#f87171',border:'#dc2626'}}}},font:{{color:'#fff',size:12,face:'Inter,sans-serif'}},shape:'diamond',borderWidth:2}},
    routine: {{color:{{background:'#06b6d4',border:'#0e7490',highlight:{{background:'#67e8f9',border:'#0891b2'}}}},font:{{color:'#fff',size:12,face:'Inter,sans-serif'}},shape:'triangle',borderWidth:2}},
    layout:  {{color:{{background:'#eab308',border:'#a16207',highlight:{{background:'#fde047',border:'#ca8a04'}}}},font:{{color:'#1a1a1a',size:12,face:'Inter,sans-serif'}},shape:'box',borderWidth:2}},
    step:    {{color:{{background:'#f43f5e',border:'#be123c',highlight:{{background:'#fb7185',border:'#e11d48'}}}},font:{{color:'#fff',size:12,face:'Inter,sans-serif'}},shape:'box',borderWidth:2}},
    variable:{{color:{{background:'#64748b',border:'#334155',highlight:{{background:'#94a3b8',border:'#475569'}}}},font:{{color:'#fff',size:11,face:'Inter,sans-serif'}},shape:'dot',borderWidth:2}}
  }},
  edges:{{
    arrows:'to',
    font:{{size:9,color:'rgba(255,255,255,0.45)',align:'middle',face:'Inter,sans-serif'}},
    color:{{color:'rgba(148,163,184,0.35)',highlight:'rgba(148,163,184,0.9)'}},
    smooth:{{type:'dynamic'}},width:1.5
  }},
  physics:{{stabilization:{{iterations:180,updateInterval:25}},barnesHut:{{gravitationalConstant:-5000,springLength:130,springConstant:0.04}},minVelocity:0.75}},
  interaction:{{hover:true,navigationButtons:true,keyboard:true,zoomView:true,tooltipDelay:150}}
}};
new vis.Network(document.getElementById('gr'),{{nodes:N,edges:E}},opts);
</script>
</body></html>"""

    srcdoc = inner.replace('"', '&quot;')
    _leg_items = [
        ('#3b82f6', 'Programa'), ('#f59e0b', 'JOB'),     ('#22c55e', 'Dataset'),
        ('#a855f7', 'Copybook'), ('#ef4444', 'DB2'),      ('#06b6d4', 'Rutina'),
        ('#eab308', 'Layout'),   ('#f43f5e', 'Step'),     ('#64748b', 'Variable'),
    ]
    chips = ''.join(
        f"<span style='display:inline-flex;align-items:center;gap:5px;"
        f"background:{c}18;color:{c};border:1px solid {c}44;"
        f"border-radius:20px;padding:3px 10px;font-size:11px;font-family:Inter,sans-serif'>"
        f"<span style='width:7px;height:7px;border-radius:50%;background:{c};display:inline-block'></span>"
        f"{n}</span>"
        for c, n in _leg_items
    )
    return (
        f'<div style="border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.12)">'
        f'<iframe srcdoc="{srcdoc}" style="width:100%;height:430px;border:0;display:block;" frameborder="0"></iframe>'
        f'<div style="display:flex;flex-wrap:wrap;gap:6px;padding:10px 14px;'
        f'background:#f8fafc;border:1px solid #e2e8f0;border-top:0;border-radius:0 0 12px 12px">'
        f'{chips}</div></div>'
    )


def build_radar_html(axes_data: dict) -> str:
    """
    Radar/spider con Chart.js + tarjetas de métricas con justificación expandible.
    Layout: izquierda = gráfico radar, derecha = cards por eje.
    """
    import json as _json

    _EMPTY = (
        "<div style='display:flex;align-items:center;justify-content:center;"
        "height:200px;background:#f8fafc;border-radius:12px;color:#94a3b8;"
        "font-style:italic;font-family:Inter,sans-serif;border:1px dashed #e2e8f0'>"
        "Consulta un programa o rutina concreta para ver el análisis de ejes.</div>"
    )
    if not axes_data:
        return _EMPTY

    labels = ['Deuda Técnica', 'Complejidad', 'Valor Negocio', 'Riesgo Seguridad', 'T. Proceso']
    _COLORS = [
        ('rgba(59,130,246,0.20)',  'rgba(59,130,246,1)',  '#3b82f6'),
        ('rgba(245,158,11,0.20)', 'rgba(245,158,11,1)',  '#f59e0b'),
        ('rgba(34,197,94,0.20)',  'rgba(34,197,94,1)',   '#22c55e'),
        ('rgba(239,68,68,0.20)',  'rgba(239,68,68,1)',   '#ef4444'),
        ('rgba(168,85,247,0.20)', 'rgba(168,85,247,1)',  '#a855f7'),
    ]

    datasets = []
    for i, (pid, ax) in enumerate(list(axes_data.items())[:5]):
        proc_raw = int(ax.get('proc_time', 0) or 0)
        proc_norm = min(int(proc_raw / 6), 100) if proc_raw > 100 else proc_raw
        values = [ax.get('debt', 0), ax.get('complexity', 0), ax.get('bv', 0),
                  ax.get('security', 0), proc_norm]
        bg, border, _ = _COLORS[i % len(_COLORS)]
        datasets.append({
            'label': pid, 'data': values,
            'backgroundColor': bg, 'borderColor': border, 'borderWidth': 2.5,
            'pointBackgroundColor': border, 'pointRadius': 5, 'pointHoverRadius': 7,
        })

    chart_cfg = {
        'type': 'radar',
        'data': {'labels': labels, 'datasets': datasets},
        'options': {
            'responsive': True, 'maintainAspectRatio': True,
            'plugins': {
                'legend': {
                    'position': 'bottom',
                    'labels': {'font': {'size': 11, 'family': 'Inter,sans-serif'}, 'boxWidth': 14}
                },
                'title': {
                    'display': True,
                    'text': 'Análisis de Ejes  (escala 0 – 100)',
                    'font': {'size': 13, 'weight': '600', 'family': 'Inter,sans-serif'},
                    'color': '#1e293b',
                },
            },
            'scales': {
                'r': {
                    'min': 0, 'max': 100,
                    'ticks': {
                        'stepSize': 25, 'backdropColor': 'transparent',
                        'font': {'size': 9, 'family': 'Inter,sans-serif'}, 'color': '#94a3b8',
                    },
                    'grid': {'color': 'rgba(203,213,225,0.6)'},
                    'angleLines': {'color': 'rgba(203,213,225,0.8)'},
                    'pointLabels': {
                        'font': {'size': 11, 'weight': '600', 'family': 'Inter,sans-serif'},
                        'color': '#374151',
                    },
                }
            },
        },
    }

    # ── Paletas de nivel ──────────────────────────────────────────────────────
    _LVL_LOW = {   # cuanto más bajo mejor (Deuda, Complejidad, Riesgo)
        'MUY_BAJO': ('background:#dcfce7;color:#166534;border:1px solid #86efac', '✓ Muy bajo'),
        'BAJO':     ('background:#f0fdf4;color:#15803d;border:1px solid #4ade80', '✓ Bajo'),
        'MEDIO':    ('background:#fef3c7;color:#92400e;border:1px solid #fcd34d', '◆ Medio'),
        'ALTO':     ('background:#fee2e2;color:#991b1b;border:1px solid #fca5a5', '⚠ Alto'),
    }
    _LVL_HIGH = {  # cuanto más alto mejor (Valor Negocio)
        'MUY_BAJO': ('background:#fee2e2;color:#991b1b;border:1px solid #fca5a5', '⚠ Muy bajo'),
        'BAJO':     ('background:#fef3c7;color:#92400e;border:1px solid #fcd34d', '◆ Bajo'),
        'MEDIO':    ('background:#f0fdf4;color:#15803d;border:1px solid #4ade80', '✓ Medio'),
        'ALTO':     ('background:#dcfce7;color:#166534;border:1px solid #86efac', '✓ Alto'),
    }
    _BAR_LOW  = {'MUY_BAJO': '#22c55e', 'BAJO': '#4ade80', 'MEDIO': '#f59e0b', 'ALTO': '#ef4444'}
    _BAR_HIGH = {'MUY_BAJO': '#ef4444', 'BAJO': '#f59e0b', 'MEDIO': '#4ade80', 'ALTO': '#22c55e'}

    def _badge(lv, high_better=False):
        d = _LVL_HIGH if high_better else _LVL_LOW
        style, label = d.get((lv or '').upper(),
                             ('background:#f1f5f9;color:#475569;border:1px solid #cbd5e1', lv or '—'))
        return (f'<span style="border-radius:20px;padding:2px 8px;font-size:10px;'
                f'font-weight:700;{style}">{label}</span>')

    def _bar(pct, color):
        return (f'<div style="background:#f1f5f9;height:5px;width:100%">'
                f'<div style="width:{pct}%;height:5px;background:{color};'
                f'border-radius:0 3px 3px 0"></div></div>')

    def _card(icon, name, score, max_val, level, rationale, high_better=False):
        pct = min(int(score / max_val * 100), 100) if max_val else 0
        lv  = (level or '').upper()
        bar_color = (_BAR_HIGH if high_better else _BAR_LOW).get(lv, '#94a3b8')
        score_disp = (f'{score}s' if max_val == 900
                      else f'{score}<span style="font-size:0.68rem;color:#94a3b8">/100</span>')
        rat_html = ''
        if rationale:
            rat_html = (
                f'<details><summary style="padding:5px 12px 4px;cursor:pointer;'
                f'color:#3b82f6;font-size:0.73rem;font-weight:500;'
                f'list-style:none;outline:none">▸ Ver justificación</summary>'
                f'<div style="padding:4px 12px 10px;font-size:0.71rem;color:#475569;'
                f'line-height:1.75;border-top:1px solid #f1f5f9">{rationale}</div></details>'
            )
        return (
            f'<div style="background:#fff;border-radius:10px;border:1px solid #e2e8f0;'
            f'overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.06);margin-bottom:6px">'
            f'<div style="display:flex;align-items:center;gap:8px;padding:8px 12px">'
            f'<span style="font-size:1rem">{icon}</span>'
            f'<span style="flex:1;font-weight:600;font-size:0.82rem;color:#1e293b;'
            f'font-family:Inter,sans-serif">{name}</span>'
            f'{_badge(lv, high_better)}'
            f'<span style="font-size:1rem;font-weight:800;color:#0f172a;margin-left:8px;'
            f'font-family:Inter,sans-serif">{score_disp}</span>'
            f'</div>'
            f'{_bar(pct, bar_color)}'
            f'{rat_html}'
            f'</div>'
        )

    # ── Construir secciones de tarjetas (hasta 3 programas) ──────────────────
    cards_html = ''
    for i, (pid, ax) in enumerate(list(axes_data.items())[:3]):
        _, _, col = _COLORS[i % len(_COLORS)]
        prog_hdr = ''
        if len(axes_data) > 1:
            prog_hdr = (f'<div style="font-size:0.75rem;font-weight:700;color:{col};'
                        f'letter-spacing:0.06em;text-transform:uppercase;'
                        f'padding:4px 0 6px;border-bottom:2px solid {col}22;'
                        f'margin-bottom:6px;font-family:Inter,sans-serif">⬡ {pid}</div>')
        pt = ax.get('proc_time', 0)
        proc_max = 900 if pt > 100 else 100
        cards_html += (
            prog_hdr
            + _card('💸', 'Deuda Técnica',   ax.get('debt', 0),       100, ax.get('debt_lv', ''),  ax.get('debt_rationale', ''))
            + _card('⚙️', 'Complejidad',      ax.get('complexity', 0), 100, ax.get('comp_lv', ''), ax.get('comp_rationale', ''))
            + _card('💼', 'Valor de Negocio', ax.get('bv', 0),          100, ax.get('bv_lv', ''),   ax.get('bv_rationale', ''),  high_better=True)
            + _card('🔐', 'Riesgo Seguridad', ax.get('security', 0),   100, ax.get('sec_lv', ''),  ax.get('sec_rationale', ''))
            + _card('⏱️', 'Tiempo Proceso',   pt, proc_max, ax.get('time_lv', ''), ax.get('time_rationale', ''))
        )

    cfg_json = _json.dumps(chart_cfg)
    inner = (
        '<!DOCTYPE html><html><head>'
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>'
        '<style>'
        '*{box-sizing:border-box;margin:0;padding:0}'
        'body{background:#f8fafc;font-family:Inter,-apple-system,sans-serif;'
        'height:100vh;overflow:hidden;padding:12px}'
        '.wrap{display:flex;gap:14px;height:calc(100vh - 24px)}'
        '.chart-col{flex:0 0 52%;display:flex;flex-direction:column;justify-content:center}'
        '.cards-col{flex:1;overflow-y:auto;padding-right:2px}'
        '.cards-col::-webkit-scrollbar{width:4px}'
        '.cards-col::-webkit-scrollbar-track{background:#f1f5f9}'
        '.cards-col::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:4px}'
        'details summary{list-style:none;outline:none}'
        'details summary::-webkit-details-marker{display:none}'
        '</style></head><body>'
        '<div class="wrap">'
        '<div class="chart-col"><canvas id="rc"></canvas></div>'
        f'<div class="cards-col">{cards_html}</div>'
        '</div>'
        f'<script>new Chart(document.getElementById(\'rc\'), {cfg_json});</script>'
        '</body></html>'
    )

    srcdoc = inner.replace('"', '&quot;')
    return (
        '<div style="border-radius:12px;overflow:hidden;border:1px solid #e2e8f0;'
        'box-shadow:0 2px 12px rgba(0,0,0,0.06)">'
        f'<iframe srcdoc="{srcdoc}" style="width:100%;height:500px;border:0;display:block;" '
        f'frameborder="0"></iframe>'
        '</div>'
    )


def get_graph_context(question: str) -> tuple:
    """
    Retorna (context_str, direct_answer).
    direct_answer: respuesta formateada directamente desde el grafo (no pasa por LLM).
    context_str:   contexto RAG para el LLM (usado solo si direct_answer está vacío).
    """
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    except Exception as e:
        return f"[ERROR conexión Neo4j: {e}]", "", "", {}

    context_parts = []
    direct_parts  = []
    _impact_debug = ""

    # Si la pregunta contiene un identificador concreto (ej: MP3C3628, MPJP001J)
    # los resultados directos son autoritativos → MODO DIRECTO (sin LLM).
    # Si es una pregunta conceptual (sin identificador) → todo va al LLM.
    import re as _re_id
    _has_specific_id = bool(_re_id.search(r'\b[A-Za-z]{2,}[0-9][A-Za-z0-9#$@-]{2,}\b', question))
    _malla_intent = _has_malla_intent(question)
    _routine_intent = _has_routine_intent(question)
    _routine_security_db2_impact = _routine_security_db2_impact_intent(question)
    _dependency_chain_mode = _dependency_chain_intent(question)
    _malla_security_data_jobs_mode = _malla_security_data_jobs_intent(question)
    _malla_focus_mode = _malla_focus(question)
    _condition_intent = any(term in question.lower() for term in ("condicion", "condición", "condiciones"))
    _condition_lookup_intent = _condition_intent and bool(
        _re_id.search(r'\b[A-Za-z0-9#$@]+-(?:OK|END|IN|OUT|DONE|ERR|ERROR)\b', question, _re_id.IGNORECASE)
    )

    with driver.session() as session:
        keywords = extract_keywords(question)

        # 0. Consulta focalizada: jobs de una malla que ejecutan programas con riesgo
        # alto de seguridad y evidencia de datos/DB2. Evita expansión genérica.
        if _malla_security_data_jobs_mode:
            limit = _requested_limit(question, default=10)
            rows = []
            used_attempt = None
            for attempt in _malla_security_data_attempts():
                rows = session.run(
                    MALLA_SECURITY_DATA_JOBS_SEARCH,
                    keywords=keywords,
                    limit=limit,
                    security_levels=attempt["security_levels"],
                    min_security_score=attempt["min_security_score"],
                    business_value_levels=attempt["business_value_levels"],
                    min_business_value_score=attempt["min_business_value_score"],
                    require_data_evidence=attempt["require_data_evidence"],
                ).data()
                used_attempt = attempt
                if rows:
                    break
            if rows:
                lines = ["Jobs de malla con programas relevantes en seguridad y datos:"]
                if used_attempt and used_attempt != _malla_security_data_attempts()[0]:
                    lines.append(
                        "No hubo coincidencias exactas con riesgo ALTO; "
                        f"relajé el criterio a: {used_attempt['label']}."
                    )
                for r in rows:
                    db2s = _clean(r.get("db2_tables", []))
                    db2_text = ", ".join(db2s[:8]) if db2s else "sin DB2 registrado; evidencia por valor de negocio"
                    lines.append(
                        f"- {r.get('job_id')} -> {r.get('program_id')}: "
                        f"seguridad {r.get('security_score')}/100 ({r.get('security_level')}); "
                        f"datos/negocio {r.get('business_value_score')}/100 ({r.get('business_value_level')}); "
                        f"DB2: {db2_text}"
                    )
                ans = "\n".join(lines)
                axes_ids = [r.get("program_id") for r in rows if r.get("program_id")]
                axes_data = fetch_axes_for_entities(axes_ids)
                driver.close()
                return ans, ans, f"Consulta focalizada malla seguridad/datos: intento={used_attempt}, ids={axes_ids}", axes_data

            ans = "No encontré jobs en esa malla ni siquiera relajando los criterios de seguridad/datos."
            driver.close()
            return ans, ans, "Consulta focalizada malla seguridad/datos: sin resultados", {}

        # 0. Ranking por ejes: debe evaluarse antes del listado general.
        # Ej: "dame la lista de los 5 programas con tiempo de proceso elevado"
        if not _has_specific_id and _processing_time_ranking_intent(question):
            q_low = question.lower()
            singular_default = 1 if any(term in q_low for term in (
                "el que", "cuál", "cual", "qué programa es", "que programa es",
                "qué rutina es", "que rutina es",
            )) else 5
            limit = _requested_limit(question, default=singular_default)
            wants_routines = any(w in q_low for w in ("rutina", "rutinas", "subprograma", "subprogramas"))
            wants_programs = any(w in q_low for w in ("programa", "programas", "program", "programs", "cobol"))
            filters = _axis_level_filters(question)

            if wants_routines and not wants_programs:
                rows = session.run(TOP_ROUTINE_PROCESSING_TIME, limit=limit).data()
                title = f"Rutinas con mayor tiempo de proceso ({len(rows)} de {limit} solicitadas):"
            elif filters:
                rows = []
                used_attempt = None
                for attempt in _relaxed_axis_attempts(filters):
                    rows = session.run(
                        FILTERED_PROGRAM_PROCESSING_TIME,
                        limit=limit,
                        min_time=attempt["min_time"],
                        debt_levels=attempt["debt_levels"],
                        complexity_levels=attempt["complexity_levels"],
                        business_value_levels=attempt["business_value_levels"],
                        security_levels=attempt["security_levels"],
                    ).data()
                    used_attempt = attempt
                    if rows:
                        break

                requested = []
                if filters.get("processing_time_level") == "ALTO":
                    requested.append("tiempo de proceso ALTO")
                if filters.get("debt_level"):
                    requested.append(f"deuda técnica {filters['debt_level']}")
                if filters.get("complexity_level"):
                    requested.append(f"complejidad {filters['complexity_level']}")
                if filters.get("business_value_level"):
                    requested.append(f"valor de negocio {filters['business_value_level']}")
                if filters.get("security_level"):
                    requested.append(f"riesgo seguridad {filters['security_level']}")

                relaxed = []
                if used_attempt:
                    if filters.get("processing_time_level"):
                        relaxed.append(f"tiempo={used_attempt['time_label']}")
                    if filters.get("debt_level"):
                        relaxed.append(f"deuda={','.join(used_attempt['debt_levels'])}")
                    if filters.get("complexity_level"):
                        relaxed.append(f"complejidad={','.join(used_attempt['complexity_levels'])}")
                    if filters.get("business_value_level"):
                        relaxed.append(f"valor negocio={','.join(used_attempt['business_value_levels'])}")
                    if filters.get("security_level"):
                        relaxed.append(f"seguridad={','.join(used_attempt['security_levels'])}")

                title = f"Programas encontrados ({len(rows)} de {limit} solicitados). Filtros pedidos: " + ", ".join(requested)
                if used_attempt and used_attempt["relaxation_step"] > 0:
                    title += "\nNo hubo coincidencias exactas; apliqué relajación por rangos: " + "; ".join(relaxed)
            else:
                rows = session.run(TOP_PROGRAM_PROCESSING_TIME, limit=limit).data()
                title = f"Programas con mayor tiempo de proceso ({len(rows)} de {limit} solicitados):"

            lines = []
            for idx, r in enumerate(rows, start=1):
                score = r.get("score")
                unit = "/100" if r.get("entity_type") == "rutina" else "s"
                level = f" ({r.get('level')})" if r.get("level") else ""
                axes_summary = ""
                if filters and r.get("entity_type") == "programa":
                    axes_summary = (
                        f" [deuda={r.get('debt_level') or '?'}, "
                        f"complejidad={r.get('complexity_level') or '?'}, "
                        f"valor negocio={r.get('business_value_level') or '?'}, "
                        f"seguridad={r.get('security_level') or '?'}]"
                    )
                rationale = r.get("rationale") or ""
                extra = f" - {rationale}" if rationale else ""
                lines.append(f"{idx}. {r.get('id')} - tiempo de proceso: {score}{unit}{level}{axes_summary}{extra}")

            if lines:
                ans = title + "\n" + "\n".join(lines)
            elif filters:
                ans = title + "\nNo encontré programas ni siquiera relajando los rangos disponibles."
            else:
                ans = title + "\nNo encontré datos de tiempo de proceso."
            axes_ids = [r.get("id") for r in rows if r.get("id")]
            axes_data = fetch_axes_for_entities(axes_ids)
            driver.close()
            return ans, ans, f"Ranking tiempo proceso: limit={limit}, filters={filters}, ids={axes_ids}", axes_data

        # 0. Detectar preguntas de listado general ("¿Qué jobs existen?", etc.)
        list_intent = _detect_list_intent(question)
        if list_intent == 'jobs':
            rows = session.run(LIST_ALL_JOBS).data()
            ids  = [r['job_id'] for r in rows if r.get('job_id')]
            ans  = f"Jobs en el sistema ({len(ids)} total):\n" + "\n".join(f"  - {j}" for j in ids)
            driver.close()
            return ans, ans, "", {}
        elif list_intent == 'programs':
            rows = session.run(LIST_ALL_PROGRAMS).data()
            ids  = [r['program_id'] for r in rows if r.get('program_id')]
            ans  = f"Programas en el sistema ({len(ids)} total):\n" + "\n".join(f"  - {p}" for p in ids)
            driver.close()
            return ans, ans, "", {}
        elif list_intent == 'datasets':
            rows = session.run(LIST_ALL_DATASETS).data()
            ids  = [r['dd_name'] for r in rows if r.get('dd_name')]
            ans  = f"Datasets en el sistema ({len(ids)} total):\n" + "\n".join(f"  - {d}" for d in ids)
            driver.close()
            return ans, ans, "", {}
        elif list_intent == 'mallas':
            rows = session.run(LIST_ALL_MALLAS).data()
            lines = [
                f"  - {r['malla_id']} ({r['object_count']} objetos, {r['job_count']} jobs)"
                for r in rows if r.get('malla_id')
            ]
            ans = f"Mallas en el sistema ({len(lines)} total):\n" + "\n".join(lines)
            driver.close()
            return ans, ans, "", {}

        # 1. Búsqueda directa en el grafo por nombre exacto (programa o job)
        # Rastrear qué program_ids y job_ids ya se incluyeron en el contexto directo
        _seen_programs = set()
        _seen_jobs     = set()
        _seen_datasets = set()
        _seen_malla_objects = set()
        _seen_malla_conditions = set()
        _seen_routines = set()

        # 1a. Búsqueda directa por dataset
        direct_ds = session.run(DIRECT_DATASET_SEARCH, keywords=keywords).data()
        for r in direct_ds:
            dsn = r.get("dataset_name")
            if not dsn or dsn in _seen_datasets:
                continue
            _seen_datasets.add(dsn)
            part = [f"Dataset: {dsn}"]
            reads_p  = _clean(r.get("read_by_programs", []))
            writes_p = _clean(r.get("written_by_programs", []))
            reads_s  = _clean(r.get("read_by_steps", []))
            writes_s = _clean(r.get("written_by_steps", []))
            if reads_p:  part.append(f"  Leído por programas:  {', '.join(reads_p[:16])}")
            if writes_p: part.append(f"  Escrito por programas: {', '.join(writes_p[:16])}")
            if reads_s:  part.append(f"  Leído por steps:     {', '.join(reads_s[:16])}")
            if writes_s: part.append(f"  Escrito por steps:   {', '.join(writes_s[:16])}")
            if _has_specific_id and not _malla_intent:
                direct_parts.append("\n".join(part))
            context_parts.append("\n".join(part))

        direct_prog = session.run(DIRECT_PROGRAM_SEARCH, keywords=keywords).data()
        for r in direct_prog:
            pid = r['program_id']
            if pid in _seen_programs:
                continue
            _seen_programs.add(pid)
            part = [f"Programa: {pid}"]
            loc          = r.get("loc")
            author       = r.get("author")
            date_written = r.get("date_written")
            app_name     = r.get("app_name")
            reads  = _clean(r.get("reads_datasets", []))
            writes = _clean(r.get("writes_datasets", []))
            jobs   = _clean(r.get("invoked_by_jobs", []))
            copies  = _clean(r.get("copybooks", []))
            db2s    = _clean(r.get("db2_tables", []))
            s_routs = _clean(r.get("static_routines", []))
            d_routs = _clean(r.get("dynamic_routines", []))
            layouts = _clean(r.get("layouts", []))
            if author:       part.append(f"  Autor:             {author}")
            if date_written: part.append(f"  Fecha escritura:   {date_written}")
            if app_name:     part.append(f"  Aplicación:        {app_name}")
            if loc is not None: part.append(f"  Líneas de código:  {loc}")
            if layouts:  part.append(f"  Layouts embebidos ({len(layouts)}): {', '.join(layouts[:16])}")
            if reads:    part.append(f"  Lee datasets:      {', '.join(reads[:8])}")
            if writes:   part.append(f"  Escribe datasets:  {', '.join(writes[:8])}")
            if jobs:     part.append(f"  Invocado por JOBs: {', '.join(jobs[:8])}")
            if copies:   part.append(f"  Copybooks:         {', '.join(copies[:8])}")
            if db2s:     part.append(f"  Tablas DB2 ({len(db2s)}):    {', '.join(db2s[:16])}")
            if s_routs:  part.append(f"  Rutinas estáticas: {', '.join(s_routs[:16])}")
            if d_routs:  part.append(f"  Rutinas dinámicas: {', '.join(d_routs[:16])}")
            if _has_specific_id and not _malla_intent:
                direct_parts.append("\n".join(part))
            context_parts.append("\n".join(part))

        direct_job = session.run(DIRECT_JOB_SEARCH, keywords=keywords).data()
        for r in direct_job:
            jid = r['job_id']
            if jid in _seen_jobs:
                continue
            _seen_jobs.add(jid)
            step_count = r.get("step_count", 0)
            part = [f"JOB: {jid}  ({step_count} step{'s' if step_count != 1 else ''})"]
            steps  = _clean(r.get("step_programs", []))
            reads  = _clean(r.get("reads_datasets", []))
            writes = _clean(r.get("writes_datasets", []))
            if steps:
                part.append(f"  Steps (en orden):")
                for st in steps:
                    part.append(f"    {st}")
            if reads:  part.append(f"  Lee datasets:       {', '.join(reads[:8])}")
            if writes: part.append(f"  Escribe datasets:   {', '.join(writes[:8])}")
            if _has_specific_id and not _malla_intent:
                direct_parts.append("\n".join(part))
            context_parts.append("\n".join(part))

        direct_step = session.run(DIRECT_STEP_SEARCH, keywords=keywords).data()
        for r in direct_step:
            part = [f"Step: {r['step_name']} (pertenece al JOB: {r['job_id']})"]
            part.append(f"  Tipo: {r.get('exec_type','')} / {r.get('activity_type','')}")
            part.append(f"  Orden: {r.get('sequence_number','')}")
            progs  = _clean(r.get("executes_programs", []))
            reads  = _clean(r.get("reads_datasets", []))
            writes = _clean(r.get("writes_datasets", []))
            if progs:  part.append(f"  Ejecuta programas:  {', '.join(progs[:8])}")
            if reads:  part.append(f"  Lee datasets:       {', '.join(reads[:8])}")
            if writes: part.append(f"  Escribe datasets:   {', '.join(writes[:8])}")
            if _has_specific_id and not _malla_intent:
                direct_parts.append("\n".join(part))
            context_parts.append("\n".join(part))

        direct_routine = session.run(DIRECT_ROUTINE_SEARCH, keywords=keywords).data()
        for r in direct_routine:
            rid = r.get("routine_id")
            if not rid or rid in _seen_routines:
                continue
            _seen_routines.add(rid)
            part = [f"Rutina: {rid}"]
            if r.get("loc") is not None:
                part.append(f"  Lineas de codigo: {r.get('loc')}")
            invoked_by = _clean(r.get("invoked_by_programs", []))
            called = _clean(r.get("called_routines", []))
            db2s = _clean(r.get("db2_tables", []))
            inputs = _clean(r.get("input_variables", []))
            outputs = _clean(r.get("output_variables", []))
            if invoked_by:
                part.append(f"  Invocada por programas: {', '.join(invoked_by[:16])}")
            if called:
                part.append(f"  Invoca rutinas: {', '.join(called[:16])}")
            if db2s:
                part.append(f"  Tablas DB2: {', '.join(db2s[:16])}")
            if inputs:
                part.append(f"  Variables entrada: {', '.join(inputs[:16])}")
            if outputs:
                part.append(f"  Variables salida: {', '.join(outputs[:16])}")
            axes_line = []
            if r.get("debt_score") is not None:
                axes_line.append(f"deuda {r.get('debt_score')}/100 {r.get('debt_level') or ''}".strip())
            if r.get("complexity_score") is not None:
                axes_line.append(f"complejidad {r.get('complexity_score')}/100 {r.get('complexity_level') or ''}".strip())
            if r.get("business_value_score") is not None:
                axes_line.append(f"valor negocio {r.get('business_value_score')}/100 {r.get('business_value_level') or ''}".strip())
            if r.get("security_score") is not None:
                axes_line.append(f"seguridad {r.get('security_score')}/100 {r.get('security_level') or ''}".strip())
            if r.get("processing_time_score") is not None:
                axes_line.append(f"tiempo proceso {r.get('processing_time_score')}/100 {r.get('processing_time_level') or ''}".strip())
            if axes_line:
                part.append(f"  Ejes: {', '.join(axes_line)}")
            if r.get("security_rationale"):
                part.append(f"  Justificacion seguridad: {r.get('security_rationale')}")
            if _has_specific_id and not _malla_intent:
                direct_parts.append("\n".join(part))
            context_parts.append("\n".join(part))

        if _dependency_chain_mode and _has_specific_id:
            rows = session.run(JOB_DEPENDENCY_CHAIN_SEARCH, keywords=keywords).data()
            if rows:
                first = rows[0]
                chain_lines = [
                    f"Cadena de dependencia para {first.get('requested_id')}:"
                ]
                if first.get("malla_id") or first.get("memname"):
                    chain_lines.append(
                        f"Malla: {first.get('malla_id') or '(sin malla)'} | Objeto: {first.get('memname') or first.get('requested_id')}"
                    )
                    if first.get("description"):
                        chain_lines.append(f"Descripción malla: {first.get('description')}")
                    inputs = _clean(first.get("input_conditions", []))
                    outputs = _clean(first.get("output_conditions", []))
                    preds = _clean(first.get("predecessors", []))
                    succs = _clean(first.get("successors", []))
                    chain_lines.append(
                        "Condiciones entrada: " + (", ".join(inputs[:16]) if inputs else "sin condiciones registradas")
                    )
                    chain_lines.append(
                        "Condiciones salida: " + (", ".join(outputs[:16]) if outputs else "sin condiciones registradas")
                    )
                    chain_lines.append(
                        "Predecesores malla: " + (", ".join(preds[:16]) if preds else "sin predecesores registrados")
                    )
                    chain_lines.append(
                        "Sucesores malla: " + (", ".join(succs[:16]) if succs else "sin sucesores registrados")
                    )
                else:
                    chain_lines.append("Malla: no encontré objeto de malla asociado al identificador.")

                job_id = first.get("job_id")
                chain_lines.append(f"JOB JCL: {job_id or 'no encontré job JCL vinculado'}")
                program_rows = [r for r in rows if r.get("program_id")]
                if program_rows:
                    chain_lines.append("Steps / programas / datasets / DB2 / rutinas / ejes:")
                    for r in program_rows[:20]:
                        pid = r.get("program_id")
                        if pid:
                            _seen_programs.add(pid)
                        step = r.get("step_name") or "(step sin nombre)"
                        order = r.get("step_order")
                        reads = _clean(r.get("reads_datasets", []))
                        writes = _clean(r.get("writes_datasets", []))
                        db2s = _clean(r.get("db2_tables", []))
                        routines = _clean(r.get("routines", []))
                        for rid in routines:
                            _seen_routines.add(str(rid).split(" ", 1)[0])
                        chain_lines.append(f"- Step {order if order is not None else '?'} {step} -> Programa {pid}")
                        if reads:
                            chain_lines.append(f"  Lee datasets: {', '.join(reads[:8])}")
                        if writes:
                            chain_lines.append(f"  Escribe datasets: {', '.join(writes[:8])}")
                        if db2s:
                            chain_lines.append(f"  DB2: {', '.join(db2s[:12])}")
                        if routines:
                            chain_lines.append(f"  Rutinas: {', '.join(routines[:12])}")
                        axes = []
                        if r.get("debt_score") is not None:
                            axes.append(f"deuda {r.get('debt_score')}/100 {r.get('debt_level') or ''}".strip())
                        if r.get("complexity_score") is not None:
                            axes.append(f"complejidad {r.get('complexity_score')}/100 {r.get('complexity_level') or ''}".strip())
                        if r.get("business_value_score") is not None:
                            axes.append(f"valor negocio {r.get('business_value_score')}/100 {r.get('business_value_level') or ''}".strip())
                        if r.get("security_score") is not None:
                            axes.append(f"seguridad {r.get('security_score')}/100 {r.get('security_level') or ''}".strip())
                        if r.get("processing_time_sec") is not None:
                            axes.append(f"tiempo proceso {r.get('processing_time_sec')}s")
                        if axes:
                            chain_lines.append(f"  Ejes impacto: {', '.join(axes)}")
                else:
                    chain_lines.append("No encontré steps/programas asociados al JOB JCL en el grafo.")

                chain_answer = "\n".join(chain_lines)
                direct_parts.append(chain_answer)
                context_parts.append(chain_answer)

        if _routine_security_db2_impact:
            rows = session.run(DIRECT_ROUTINE_SECURITY_DB2_IMPACT_SEARCH).data()
            if rows:
                lines = ["Rutinas invocadas por programas de impacto, con riesgo de seguridad alto y acceso DB2:"]
                for r in rows:
                    if r.get("routine_id"):
                        _seen_routines.add(r.get("routine_id"))
                    programs = _clean(r.get("programs", []))
                    db2s = _clean(r.get("db2_tables", []))
                    line = (
                        f"- {r.get('routine_id')}: seguridad {r.get('security_score')}/100 "
                        f"({r.get('security_level')}); valor negocio rutina "
                        f"{r.get('business_value_score')}/100 ({r.get('business_value_level')}); "
                        f"programas: {', '.join(programs[:6])}; "
                        f"DB2: {', '.join(db2s[:8])}"
                    )
                    if r.get("security_rationale"):
                        line += f"; motivo seguridad: {r.get('security_rationale')}"
                    lines.append(line)
                answer = "\n".join(lines)
                direct_parts.append(answer)
                context_parts.append(answer)
            else:
                answer = (
                    "No encontré rutinas que cumplan simultáneamente: invocadas por programas "
                    "de impacto, riesgo de seguridad alto y acceso a DB2."
                )
                direct_parts.append(answer)
                context_parts.append(answer)

        # 1b. Búsqueda directa y expansión de mallas Control-M
        direct_malla = session.run(DIRECT_MALLA_OBJECT_SEARCH, keywords=keywords).data()
        for r in direct_malla:
            oid = r.get("object_id")
            if not oid or oid in _seen_malla_objects:
                continue
            _seen_malla_objects.add(oid)
            part = [
                f"MallaObject: {r.get('memname')} (malla: {r.get('malla_id')}, tipo: {r.get('typ')})"
            ]
            if r.get("source_file"):
                part.append(f"  Archivo fuente: {r.get('source_file')}")
            if r.get("group_name") or r.get("table_name"):
                part.append(f"  Grupo/Tabla: {r.get('group_name','')} / {r.get('table_name','')}")
            if r.get("description"):
                part.append(f"  Descripción: {r.get('description')}")
            inputs = _clean(r.get("input_conditions", []))
            outputs = _clean(r.get("output_conditions", []))
            preds = _clean(r.get("predecessors", []))
            succs = _clean(r.get("successors", []))
            linked_jobs = _clean(r.get("linked_jobs", []))
            if inputs:
                part.append(f"  Condiciones entrada: {', '.join(inputs[:16])}")
            if outputs:
                part.append(f"  Condiciones salida:  {', '.join(outputs[:16])}")
            if preds:
                part.append(f"  Depende de:          {', '.join(preds[:16])}")
            if succs:
                part.append(f"  Habilita a:          {', '.join(succs[:16])}")
            if linked_jobs:
                part.append(f"  Vinculado a JOB JCL: {', '.join(linked_jobs[:8])}")
            # Las mallas son datos estructurados; si se menciona un ID concreto o la pregunta
            # habla de mallas, devolver directo es más fiable que sintetizar desde cero.
            if (_has_specific_id or _malla_intent) and not _dependency_chain_mode:
                memname = r.get("memname") or oid
                malla_id = r.get("malla_id")
                suffix = f" en la malla {malla_id}" if malla_id else ""
                if _malla_focus_mode == "successors":
                    names = _malla_edge_names(succs)
                    direct_parts.append(
                        f"Jobs habilitados por {memname}{suffix}: "
                        + (", ".join(names) if names else "no se encontraron jobs habilitados.")
                    )
                elif _malla_focus_mode == "predecessors":
                    names = _malla_edge_names(preds)
                    direct_parts.append(
                        f"Jobs que habilitan a {memname}{suffix}: "
                        + (", ".join(names) if names else "no se encontraron jobs predecesores.")
                    )
                elif _malla_focus_mode == "inputs":
                    direct_parts.append(
                        f"Condiciones de entrada de {memname}{suffix}: "
                        + (", ".join(inputs) if inputs else "no se encontraron condiciones de entrada.")
                    )
                elif _malla_focus_mode == "outputs":
                    direct_parts.append(
                        f"Condiciones de salida de {memname}{suffix}: "
                        + (", ".join(outputs) if outputs else "no se encontraron condiciones de salida.")
                    )
                else:
                    direct_parts.append("\n".join(part))
            if not _dependency_chain_mode:
                context_parts.append("\n".join(part))

        direct_cond = session.run(DIRECT_MALLA_CONDITION_SEARCH, keywords=keywords).data()
        for r in direct_cond:
            cid = r.get("condition_id")
            if not cid or cid in _seen_malla_conditions:
                continue
            _seen_malla_conditions.add(cid)
            part = [f"Condición de malla: {r.get('condition_name')} ({r.get('odat')})"]
            if r.get("malla_id"):
                part.append(f"  Malla: {r.get('malla_id')}")
            if r.get("source_file"):
                part.append(f"  Archivo fuente: {r.get('source_file')}")
            producers = _clean(r.get("producers", []))
            consumers = _clean(r.get("consumers", []))
            if producers:
                part.append(f"  Producida por: {', '.join(producers[:16])}")
            if consumers:
                part.append(f"  Requerida por: {', '.join(consumers[:16])}")
            if (_has_specific_id or _malla_intent) and _condition_lookup_intent:
                direct_parts.append("\n".join(part))
            context_parts.append("\n".join(part))

        # 1c. Expansion GraphRAG multi-salto para impacto / lineage.
        impact_sections, impact_direct, _impact_debug, _impact_programs = collect_impact_context(
            session,
            keywords,
            include_direct=((_has_specific_id or has_impact_intent(question)) and not _malla_intent and not _routine_intent),
        )
        if impact_sections and not _routine_intent:
            context_parts.extend(impact_sections)
        if impact_direct and not _routine_intent:
            direct_parts.extend(impact_direct)
        _seen_programs.update(_impact_programs)

        # 2. Búsqueda vectorial semántica en documentos RAG
        # ── Búsqueda RAG dual: vectorial + texto por keywords ──────────────────
        _rag_debug = []   # info para el debug panel
        if _impact_debug:
            _rag_debug.append(_impact_debug)
        docs_by_id: dict = {}   # id -> doc (dedup)

        # A) Búsqueda vectorial semántica
        query_vector = get_embedding_openai(question)
        if query_vector:
            try:
                vec_rows = session.run(
                    VECTOR_SEARCH, query_vector=query_vector, top_k=TOP_K
                ).data()
                for r in vec_rows:
                    docs_by_id[r["doc_id"]] = r
                scores_str = ", ".join(f"{r['score']:.3f}" for r in vec_rows[:4])
                _rag_debug.append(f"Vector search: {len(vec_rows)} docs (scores: {scores_str})")
            except Exception as e:
                _rag_debug.append(f"Vector search ERROR: {e}")
        else:
            _rag_debug.append("Vector search: sin embedding (OpenAI no disponible)")

        # B) Búsqueda por texto en keywords — SIEMPRE se ejecuta en paralelo
        try:
            txt_rows = session.run(
                """
                UNWIND $keywords AS kw
                MATCH (doc:RagDocument)
                WHERE toLower(doc.text) CONTAINS toLower(kw)
                RETURN DISTINCT doc.id AS doc_id, doc.type AS doc_type,
                       doc.text AS doc_text, 0.0 AS score
                LIMIT $top_k
                """,
                keywords=keywords, top_k=TOP_K
            ).data()
            nuevos = [r for r in txt_rows if r["doc_id"] not in docs_by_id]
            for r in nuevos:
                docs_by_id[r["doc_id"]] = r
            _rag_debug.append(f"Text search (keywords={keywords}): {len(txt_rows)} docs, {len(nuevos)} nuevos")
        except Exception as e:
            _rag_debug.append(f"Text search ERROR: {e}")

        docs = list(docs_by_id.values())
        _rag_debug.append(f"Total RAG docs a expandir: {len(docs)}")

        for doc in docs:
            doc_id   = doc["doc_id"]
            doc_type = doc["doc_type"]
            doc_text = doc["doc_text"]

            # Header limpio: tipo semántico sin exponer el doc_id (evita que el LLM lo confunda con nombre real)
            part = [doc_text]

            # Expansión según tipo — omitir si el programa/job ya fue incluido via búsqueda directa
            if doc_type in ("cobol_program", "cobol_variables_input",
                            "cobol_variables_output", "cobol_embedded_layout",
                            "cobol_copybook", "cobol_dataset"):
                rows = session.run(EXPAND_COBOL_PROGRAM, doc_id=doc_id).data()
                for r in rows:
                    pid = r.get("program_id")
                    if not pid or pid in _seen_programs:
                        continue
                    _seen_programs.add(pid)
                    part.append(f"Programa: {r['program_id']}")
                    reads  = _clean(r.get("reads_datasets", []))
                    writes = _clean(r.get("writes_datasets", []))
                    jobs   = _clean(r.get("invoked_by_jobs", []))
                    copies = _clean(r.get("copybooks", []))
                    db2s   = _clean(r.get("db2_tables", []))
                    if reads:  part.append(f"  Lee datasets:      {', '.join(reads[:8])}")
                    if writes: part.append(f"  Escribe datasets:  {', '.join(writes[:8])}")
                    if jobs:   part.append(f"  Invocado por JOBs: {', '.join(jobs[:8])}")
                    if copies: part.append(f"  Copybooks:         {', '.join(copies[:8])}")
                    if db2s:   part.append(f"  Tablas DB2:        {', '.join(db2s[:8])}")

            elif doc_type in ("jcl_job", "jcl_step"):
                rows = session.run(EXPAND_JCL_JOB, doc_id=doc_id).data()
                for r in rows:
                    jid = r.get("job_id")
                    if not jid or jid in _seen_jobs:
                        continue
                    _seen_jobs.add(jid)
                    part.append(f"JOB: {r['job_id']}")
                    steps  = _clean(r.get("step_programs", []))
                    reads  = _clean(r.get("reads_datasets", []))
                    writes = _clean(r.get("writes_datasets", []))
                    if steps:  part.append(f"  Steps -> Programas: {', '.join(steps[:8])}")
                    if reads:  part.append(f"  Lee datasets:       {', '.join(reads[:8])}")
                    if writes: part.append(f"  Escribe datasets:   {', '.join(writes[:8])}")

            elif doc_type == "jcl_dataset":
                rows = session.run(EXPAND_JCL_DATASET, doc_id=doc_id).data()
                for r in rows:
                    if r.get("dataset_name"):
                        part.append(f"Dataset: {r['dataset_name']}")
                        rsteps  = _clean(r.get("read_by_steps", []))
                        wsteps  = _clean(r.get("written_by_steps", []))
                        rjobs   = _clean(r.get("read_by_jobs", []))
                        wjobs   = _clean(r.get("written_by_jobs", []))
                        # Mostrar step_name (antes del punto) no el step_id completo
                        rstep_names = [s.split('.')[-1] if '.' in s else s for s in rsteps]
                        wstep_names = [s.split('.')[-1] if '.' in s else s for s in wsteps]
                        if rstep_names: part.append(f"  Leído por steps:   {', '.join(rstep_names[:8])}")
                        if wstep_names: part.append(f"  Escrito por steps: {', '.join(wstep_names[:8])}")
                        if rjobs:   part.append(f"  Leído por JOBs:    {', '.join(rjobs[:8])}")
                        if wjobs:   part.append(f"  Escrito por JOBs:  {', '.join(wjobs[:8])}")

            elif doc_type == "malla_object":
                rows = session.run(EXPAND_MALLA_OBJECT, doc_id=doc_id).data()
                for r in rows:
                    oid = r.get("object_id")
                    if not oid or oid in _seen_malla_objects:
                        continue
                    _seen_malla_objects.add(oid)
                    part.append(f"MallaObject: {r.get('memname')} (malla: {r.get('malla_id')})")
                    inputs = _clean(r.get("input_conditions", []))
                    outputs = _clean(r.get("output_conditions", []))
                    preds = _clean(r.get("predecessors", []))
                    succs = _clean(r.get("successors", []))
                    linked_jobs = _clean(r.get("linked_jobs", []))
                    if inputs: part.append(f"  Condiciones entrada: {', '.join(inputs[:16])}")
                    if outputs: part.append(f"  Condiciones salida:  {', '.join(outputs[:16])}")
                    if preds: part.append(f"  Depende de:          {', '.join(preds[:16])}")
                    if succs: part.append(f"  Habilita a:          {', '.join(succs[:16])}")
                    if linked_jobs: part.append(f"  Vinculado a JOB JCL: {', '.join(linked_jobs[:8])}")

            elif doc_type == "routine":
                rows = session.run(EXPAND_ROUTINE, doc_id=doc_id).data()
                for r in rows:
                    rid = r.get("routine_id")
                    if not rid or rid in _seen_routines:
                        continue
                    _seen_routines.add(rid)
                    part.append(f"Rutina: {rid}")
                    invoked_by = _clean(r.get("invoked_by_programs", []))
                    called = _clean(r.get("called_routines", []))
                    db2s = _clean(r.get("db2_tables", []))
                    inputs = _clean(r.get("input_variables", []))
                    outputs = _clean(r.get("output_variables", []))
                    if invoked_by: part.append(f"  Invocada por programas: {', '.join(invoked_by[:16])}")
                    if called: part.append(f"  Invoca rutinas: {', '.join(called[:16])}")
                    if db2s: part.append(f"  Tablas DB2: {', '.join(db2s[:16])}")
                    if inputs: part.append(f"  Variables entrada: {', '.join(inputs[:16])}")
                    if outputs: part.append(f"  Variables salida: {', '.join(outputs[:16])}")
                    axes_line = []
                    if r.get("debt_score") is not None:
                        axes_line.append(f"deuda {r.get('debt_score')}/100 {r.get('debt_level') or ''}".strip())
                    if r.get("complexity_score") is not None:
                        axes_line.append(f"complejidad {r.get('complexity_score')}/100 {r.get('complexity_level') or ''}".strip())
                    if r.get("business_value_score") is not None:
                        axes_line.append(f"valor negocio {r.get('business_value_score')}/100 {r.get('business_value_level') or ''}".strip())
                    if r.get("security_score") is not None:
                        axes_line.append(f"seguridad {r.get('security_score')}/100 {r.get('security_level') or ''}".strip())
                    if r.get("processing_time_score") is not None:
                        axes_line.append(f"tiempo proceso {r.get('processing_time_score')}/100 {r.get('processing_time_level') or ''}".strip())
                    if axes_line: part.append(f"  Ejes: {', '.join(axes_line)}")
                    if r.get("security_rationale"):
                        part.append(f"  Justificacion seguridad: {r.get('security_rationale')}")

            context_parts.append("\n".join(part))

    # ── Obtener ejes de análisis desde Neo4j ─────────────────────────────────
    # Combina IDs del grafo + IDs extraídos directamente de la pregunta
    import re as _re_prog
    _question_prog_ids = {
        _id.upper()
        for _id in _re_prog.findall(r'\b[A-Za-z]{2,}[0-9][A-Za-z0-9#$@-]{2,}\b', question)
    }
    _all_axes_ids = list(set(_seen_programs) | _question_prog_ids)
    _all_routine_axes_ids = list(
        set(_seen_routines)
        | (_question_prog_ids if (_routine_intent or _has_specific_id) else set())
    )

    axes_data = {}
    _axes_error = ""
    try:
        if _all_axes_ids:
            print(f"[AXES] Buscando ejes para: {_all_axes_ids}")
            with driver.session() as _s_axes:
                _axes_rows = _s_axes.run(
                    PROGRAM_AXES_QUERY, program_ids=_all_axes_ids
                ).data()
            print(f"[AXES] Resultado Neo4j: {_axes_rows}")
            for _r in _axes_rows:
                _pid = _r['program_id']
                axes_data[_pid] = {
                    'debt':        int(_r.get('debt_score') or 0),
                    'debt_lv':     _r.get('debt_level') or '',
                    'complexity':  int(_r.get('complexity_score') or 0),
                    'comp_lv':     _r.get('complexity_level') or '',
                    'bv':          int(_r.get('bv_score') or 0),
                    'bv_lv':       _r.get('bv_level') or '',
                    'bv_rationale':  _r.get('bv_rationale') or '',
                    'security':      int(_r.get('sec_score') or 0),
                    'sec_lv':        _r.get('sec_level') or '',
                    'sec_rationale': _r.get('sec_rationale') or '',
                    'proc_time':     int(_r.get('proc_time') or 0),
                    'time_rationale':_r.get('time_rationale') or '',
                    'debt_rationale':_r.get('debt_rationale') or '',
                    'comp_rationale':_r.get('comp_rationale') or '',
                }
            _axes_error = f"Ejes (Neo4j): buscados={_all_axes_ids} → encontrados={list(axes_data.keys())}"
            print(f"[AXES] axes_data={axes_data}")
        else:
            _axes_error = "Ejes: sin IDs de programas para consultar"
            print("[AXES] _all_axes_ids vacio")
    except Exception as _e_axes:
        _axes_error = f"Ejes ERROR: {_e_axes}"
        print(f"[AXES] EXCEPCION: {_e_axes}")

    try:
        if _all_routine_axes_ids:
            print(f"[AXES] Buscando ejes de rutinas para: {_all_routine_axes_ids}")
            with driver.session() as _s_axes:
                _routine_axes_rows = _s_axes.run(
                    ROUTINE_AXES_QUERY, routine_ids=_all_routine_axes_ids
                ).data()
            for _r in _routine_axes_rows:
                _rid = _r['routine_id']
                axes_data[_rid] = {
                    'entity_type':   'routine',
                    'debt':          int(_r.get('debt_score') or 0),
                    'debt_lv':       _r.get('debt_level') or '',
                    'complexity':    int(_r.get('complexity_score') or 0),
                    'comp_lv':       _r.get('complexity_level') or '',
                    'bv':            int(_r.get('bv_score') or 0),
                    'bv_lv':         _r.get('bv_level') or '',
                    'bv_rationale':  _r.get('bv_rationale') or '',
                    'security':      int(_r.get('sec_score') or 0),
                    'sec_lv':        _r.get('sec_level') or '',
                    'sec_rationale': _r.get('sec_rationale') or '',
                    'proc_time':     int(_r.get('proc_time') or 0),
                    'time_lv':       _r.get('proc_time_level') or '',
                    'time_rationale':_r.get('time_rationale') or '',
                    'debt_rationale':_r.get('debt_rationale') or '',
                    'comp_rationale':_r.get('comp_rationale') or '',
                }
            _axes_error += (
                f"\nEjes rutinas (Neo4j): buscados={_all_routine_axes_ids} "
                f"→ encontrados={[r['routine_id'] for r in _routine_axes_rows]}"
            )
            print(f"[AXES] routine axes merged={list(axes_data.keys())}")
    except Exception as _e_axes:
        _axes_error += f"\nEjes rutinas ERROR: {_e_axes}"
        print(f"[AXES] EXCEPCION rutinas: {_e_axes}")

    # ── Inyectar ejes en el contexto textual (para el LLM) ───────────────────
    # Sin esto, GPT no "ve" los scores y no puede explicarlos.
    for _pid, _ax in axes_data.items():
        _bv_rat   = _ax.get('bv_rationale', '')
        _sec_rat  = _ax.get('sec_rationale', '')
        _time_rat = _ax.get('time_rationale', '')
        _debt_rat = _ax.get('debt_rationale', '')
        _comp_rat = _ax.get('comp_rationale', '')
        _entity_label = "rutina" if _ax.get('entity_type') == 'routine' else "programa"
        _time_unit = "/100" if _ax.get('entity_type') == 'routine' else "s"
        _axes_text = (
            f"Ejes de análisis de la {_entity_label} {_pid}:\n"
            f"  Deuda Técnica:    {_ax['debt']}/100 ({_ax['debt_lv']})\n"
            + (f"  Justificacion Deuda: {_debt_rat}\n" if _debt_rat else "")
            + f"  Complejidad:      {_ax['complexity']}/100 ({_ax['comp_lv']})\n"
            + (f"  Justificacion Complejidad: {_comp_rat}\n" if _comp_rat else "")
            + f"  Valor de Negocio: {_ax['bv']}/100 ({_ax['bv_lv']})\n"
            + (f"  Justificacion BV: {_bv_rat}\n" if _bv_rat else "")
            + f"  Riesgo Seguridad: {_ax['security']}/100 ({_ax['sec_lv']})\n"
            + (f"  Justificacion Seguridad: {_sec_rat}\n" if _sec_rat else "")
            + f"  Tiempo Proceso:   {_ax['proc_time']}{_time_unit}\n"
            + (f"  Justificacion Tiempo: {_time_rat}" if _time_rat else "")
        )
        context_parts.append(_axes_text)
        if _has_specific_id and not _malla_intent:
            direct_parts.append(_axes_text)

    driver.close()
    context_str   = "\n\n".join(context_parts) if context_parts else f"No se encontraron datos para: {', '.join(keywords)}"
    direct_answer = "\n\n".join(direct_parts)
    _rag_lines    = list(_rag_debug) if '_rag_debug' in dir() else []
    _rag_lines.append(_axes_error)
    rag_debug_str = "\n".join(_rag_lines)
    print(f"[CTX] direct_answer (primeros 400 chars):\n{direct_answer[:400]}")
    print(f"[CTX] context_str (primeros 400 chars):\n{context_str[:400]}")
    return context_str, direct_answer, rag_debug_str, axes_data

# ══════════════════════════════════════════════════════════════════════════════
# LLAMADA A OPENAI
# ══════════════════════════════════════════════════════════════════════════════
def _build_messages(context: str, question: str, history: list) -> list:
    """Construye la lista de mensajes para la API de OpenAI."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history[-12:]:
        if msg["role"] in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    if context and context.strip() and context != "No se encontro informacion relevante en el grafo.":
        ctx_block = (
            f"=== DATOS DEL GRAFO (UNICOS HECHOS PERMITIDOS) ===\n"
            f"{context}\n"
            f"=== FIN DE DATOS ===\n"
        )
    else:
        ctx_block = "=== DATOS DEL GRAFO ===\n(sin resultados)\n=== FIN DE DATOS ===\n"
    messages.append({"role": "user", "content": f"{ctx_block}\nUsando EXCLUSIVAMENTE los datos de arriba, responde:\n{question}"})
    return messages


def ask_openai(context: str, question: str, history: list) -> str:
    """Llama a la API de OpenAI Chat (GPT-4o, GPT-4, etc.) via requests."""
    messages = _build_messages(context, question, history)
    try:
        resp = requests.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": OPENAI_CHAT_MODEL, "messages": messages, "temperature": 0.1},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except requests.exceptions.ConnectionError:
        return "No se pudo conectar a la API de OpenAI. Verifica tu conexion a Internet."
    except requests.exceptions.HTTPError as e:
        detail = ""
        try: detail = resp.json().get("error", {}).get("message", "")
        except Exception: pass
        return f"Error OpenAI {resp.status_code}: {detail or str(e)}"
    except Exception as e:
        return f"Error al llamar a OpenAI: {e}"


def get_embedding_openai(text: str) -> list:
    """Obtiene el embedding de un texto usando la API de OpenAI."""
    try:
        resp = requests.post(
            f"{OPENAI_BASE_URL}/embeddings",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": OPENAI_EMBED_MODEL, "input": text[:8000]},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception:
        return []


# Palabras que indican intención analítica/explicativa → siempre pasan por el LLM
_EXPLAIN_WORDS = {
    "explica","explicame","explícame","explicar","porque","por qué","porqué",
    "por que","razon","razón","motivo","analiza","analízame","analisis",
    "análisis","resume","resumen","describe","descripcion","descripción",
    "detalla","detalle","compara","comparar","evalua","evalúa","evaluar",
    "justifica","justificar","interpreta","interpretar","que hace","qué hace",
    "como funciona","cómo funciona","que significa","qué significa",
    "cuales son","cuáles son","segun","según","basado","basándose",
    "profundiza","amplia","explaya","desarrolla","argumen",
}


def _has_explain_intent(question: str) -> bool:
    """True si la pregunta pide una explicación/análisis (no solo mostrar datos)."""
    import re as _re
    q_low = question.lower()
    # Comprobar palabras sueltas
    words = set(_re.findall(r'[a-záéíóúüñ]+', q_low))
    if words & _EXPLAIN_WORDS:
        return True
    # Comprobar frases compuestas
    for phrase in ("por que", "por qué", "que hace", "qué hace",
                   "como funciona", "cómo funciona", "cuales son", "cuáles son",
                   "que significa", "qué significa", "más complejo", "mas complejo",
                   "más crítico", "mas crítico"):
        if phrase in q_low:
            return True
    return False


def _strategic_decision_intent(question: str) -> bool:
    q = question.lower()
    return any(term in q for term in (
        "optimiza", "optimización", "optimizacion", "optimizar",
        "migración", "migracion", "migrar", "modernización", "modernizacion",
        "modernizar", "candidato", "candidatos", "priorizar", "priorización",
        "priorizacion", "plan de ejecución", "plan de ejecucion",
        "decisión estratégica", "decision estrategica", "quick win",
        "quick wins", "replatform", "rehosting", "refactorización",
        "refactorizacion", "rearchitecting", "repurchase", "saas",
        "low-code", "retain", "api-first", "convivencia", "encapsulamiento",
    ))


# ══════════════════════════════════════════════════════════════════════════════
# FUNCION PRINCIPAL DEL CHAT
# ══════════════════════════════════════════════════════════════════════════════
def chat_fn(question: str, history: list):
    """Función que Gradio llama en cada mensaje."""
    if not question.strip():
        return "", history

    # 1. Buscar contexto en el grafo
    context, direct_answer, rag_debug, axes_data = get_graph_context(question)

    keywords_used = extract_keywords(question)
    strategic = _strategic_decision_intent(question)
    explain = _has_explain_intent(question) or strategic

    if strategic:
        context = "\n\n".join(x for x in (context, STRATEGIC_DECISION_MATRIX) if x and x.strip())
        if direct_answer.strip():
            direct_answer = "\n\n".join((direct_answer, STRATEGIC_DECISION_MATRIX))

    print(f"\n{'='*60}")
    print(f"[CHAT] pregunta: {question[:80]}")
    print(f"[CHAT] explain={explain} strategic={strategic} direct_len={len(direct_answer)}  context_len={len(context)}")

    # 2. Decidir modo de respuesta:
    #    MODO HÍBRIDO  : hay datos directos del grafo Y la pregunta pide una explicación
    #                    → pasar los datos al LLM para que los interprete
    #    MODO DIRECTO  : hay datos directos pero la pregunta solo pide mostrarlos
    #    MODO OPENAI   : no hay datos directos → búsqueda RAG + GPT
    if direct_answer.strip() and explain:
        # Combinar datos directos + contexto RAG como fuente para el LLM
        combined_context = direct_answer
        if context and context.strip() and context != direct_answer:
            combined_context = direct_answer + "\n\n" + context
        print(f"[MODO] HÍBRIDO  combined_context (600):\n{combined_context[:600]}")
        print(f"[MODO] HÍBRIDO  'Ejes' en combined_context: {'Ejes de análisis' in combined_context}")
        print(f"[MODO] HÍBRIDO  'Valor de Negocio' en combined_context: {'Valor de Negocio' in combined_context}")
        answer = ask_openai(combined_context, question, history)
        debug_info = (
            f"[MODO HÍBRIDO \u2014 datos grafo + LLM]\n\n"
            f"KEYWORDS: {keywords_used}\n\n"
            f"RAG:\n{rag_debug}\n\n"
            f"CONTEXTO ENVIADO A GPT (completo):\n{combined_context}"
        )
    elif direct_answer.strip():
        print(f"[MODO] DIRECTO  direct_answer (600):\n{direct_answer[:600]}")
        answer = direct_answer
        debug_info = (
            f"[MODO DIRECTO \u2014 sin LLM]\n\n"
            f"KEYWORDS: {keywords_used}\n\n"
            f"RAG:\n{rag_debug}\n\n"
            f"CONTEXTO DIRECTO:\n{direct_answer}"
        )
    else:
        print(f"[MODO] OPENAI  context (600):\n{context[:600]}")
        answer = ask_openai(context, question, history)
        debug_info = (
            f"[MODO OPENAI \u2014 {OPENAI_CHAT_MODEL}]\n\n"
            f"KEYWORDS: {keywords_used}\n\n"
            f"RAG:\n{rag_debug}\n\n"
            f"CONTEXTO ENVIADO A GPT (completo):\n{context}"
        )

    # Enriquecer el debug con el modo detectado para facilitar diagnóstico
    debug_info = (
        f"{'[EXPLICACIÓN SOLICITADA]' if explain else ''}\n{debug_info}".strip()
    )
    if strategic:
        debug_info = f"[MATRIZ ESTRATÉGICA APLICADA]\n{debug_info}"

    # Si el LLM eligió una entidad concreta desde un ranking semántico, enfocar el
    # radar en esa entidad final y no en candidatos intermedios recuperados por RAG.
    answer_entity_ids = _extract_entity_ids_from_text(answer)
    focused_axes = fetch_axes_for_entities(answer_entity_ids)
    if focused_axes:
        axes_data = focused_axes
        debug_info += (
            "\n\n[EJES ENFOCADOS POR RESPUESTA]\n"
            f"IDs respuesta: {answer_entity_ids}\n"
            f"Ejes mostrados: {list(focused_axes.keys())}"
        )

    # 3. Actualizar historial en formato Gradio 6 (dicts role/content)
    history.append({"role": "user",      "content": question})
    history.append({"role": "assistant", "content": answer})

    # 4. Construir grafo visual de dependencias.
    # Usar también el contexto estructurado evita perder grafo cuando el LLM responde en prosa.
    graph_source = "\n\n".join(x for x in (direct_answer, context, answer) if x and x.strip())
    graph_html = build_graph_html(parse_answer_to_graph(graph_source))

    # 5. Construir diagrama de telaraña con los ejes de análisis
    radar_html = build_radar_html(axes_data)

    return "", history, graph_html, debug_info, radar_html


# ══════════════════════════════════════════════════════════════════════════════
# INTERFAZ GRADIO
# ══════════════════════════════════════════════════════════════════════════════
def build_ui():
    _empty_graph = (
        "<div style='display:flex;align-items:center;justify-content:center;"
        "height:200px;background:#f8fafc;border-radius:12px;color:#94a3b8;"
        "font-style:italic;font-family:Inter,sans-serif;border:1px dashed #e2e8f0'>"
        "Realiza una consulta en el Chat para visualizar las dependencias.</div>"
    )
    _empty_radar = (
        "<div style='display:flex;align-items:center;justify-content:center;"
        "height:200px;background:#f8fafc;border-radius:12px;color:#94a3b8;"
        "font-style:italic;font-family:Inter,sans-serif;border:1px dashed #e2e8f0'>"
        "Consulta un programa concreto (ej: MP4C0370) para ver el análisis de ejes.</div>"
    )

    with gr.Blocks(title="GraphRAG COBOL/JCL") as demo:

        # ── Header ────────────────────────────────────────────────────────────
        gr.HTML(f"""
        <div style="background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 55%,#0f172a 100%);
                    border-radius:14px;padding:24px 32px;margin-bottom:12px;
                    border-left:5px solid #3b82f6;
                    box-shadow:0 4px 24px rgba(0,0,0,0.25);
                    display:flex;align-items:center;gap:20px">
          <div style="font-size:2.8rem;line-height:1">🖥️</div>
          <div style="flex:1">
            <div style="color:#fff;font-size:1.45rem;font-weight:800;
                        letter-spacing:-0.02em;font-family:Inter,-apple-system,sans-serif">
              GraphRAG &mdash; Asistente COBOL / JCL
            </div>
            <div style="color:#94a3b8;font-size:0.875rem;margin-top:5px;
                        font-family:Inter,-apple-system,sans-serif">
              Consultas en lenguaje natural &nbsp;&middot;&nbsp; Neo4j
              &nbsp;&middot;&nbsp; OpenAI {OPENAI_CHAT_MODEL}
              &nbsp;&middot;&nbsp; 111 programas &nbsp;&middot;&nbsp; 5 ejes de análisis
            </div>
            <div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">
              <span style="background:rgba(59,130,246,.18);color:#93c5fd;
                           border:1px solid rgba(59,130,246,.3);border-radius:20px;
                           padding:2px 10px;font-size:0.72rem;font-family:Inter,sans-serif">
                Neo4j AuraDB</span>
              <span style="background:rgba(16,185,129,.18);color:#6ee7b7;
                           border:1px solid rgba(16,185,129,.3);border-radius:20px;
                           padding:2px 10px;font-size:0.72rem;font-family:Inter,sans-serif">
                GPT-4o</span>
              <span style="background:rgba(139,92,246,.18);color:#c4b5fd;
                           border:1px solid rgba(139,92,246,.3);border-radius:20px;
                           padding:2px 10px;font-size:0.72rem;font-family:Inter,sans-serif">
                GraphRAG</span>
              <span style="background:rgba(245,158,11,.18);color:#fcd34d;
                           border:1px solid rgba(245,158,11,.3);border-radius:20px;
                           padding:2px 10px;font-size:0.72rem;font-family:Inter,sans-serif">
                Deuda &middot; Complejidad &middot; Valor &middot; Riesgo &middot; Tiempo</span>
            </div>
          </div>
        </div>
        """)

        state = gr.State([])

        # ── Chat ──────────────────────────────────────────────────────────────
        chatbot = gr.Chatbot(
            label="",
            height=480,
            elem_id="chatbot",
        )
        with gr.Row():
            txt_input = gr.Textbox(
                placeholder="Ej: ¿Qué hace MP4C0370?  ·  ¿Cuál es la deuda técnica de MP3C3628?  ·  ¿Qué jobs usan VSAM?",
                show_label=False,
                scale=9,
                elem_id="txt-input",
            )
            btn_send = gr.Button("➤ Enviar", variant="primary", scale=1, elem_id="btn-send")
        with gr.Row():
            btn_clear = gr.Button("🗑️  Nueva conversación", variant="secondary", elem_id="btn-clear")

        # ── Tabs: Grafo + Ejes ────────────────────────────────────────────────
        with gr.Tabs():
            with gr.TabItem("🔗  Grafo de Dependencias"):
                graph_out = gr.HTML(value=_empty_graph)
            with gr.TabItem("📊  Análisis de Ejes"):
                radar_out = gr.HTML(value=_empty_radar)

        with gr.Accordion("⚙️  Configuración del sistema", open=False):
            gr.Markdown(f"""
| Parámetro | Valor |
|---|---|
| Neo4j URI | `{NEO4J_URI}` |
| Modelo chat | `{OPENAI_CHAT_MODEL}` |
| Modelo embedding | `{OPENAI_EMBED_MODEL}` |
| Documentos RAG por consulta | `{TOP_K}` |

Para cambiar la configuración, edita las constantes al inicio de `chat_rag.py`.
""")

        with gr.Accordion("🔍  Debug — contexto enviado al LLM", open=False):
            debug_out = gr.Textbox(
                label="Contexto RAG + keywords extraídas",
                lines=14,
                max_lines=30,
                interactive=False,
                placeholder="Aparecerá tras cada consulta...",
                elem_id="debug-out",
            )

        # ── Eventos ───────────────────────────────────────────────────────────
        btn_send.click(
            fn=chat_fn,
            inputs=[txt_input, state],
            outputs=[txt_input, chatbot, graph_out, debug_out, radar_out],
        )
        txt_input.submit(
            fn=chat_fn,
            inputs=[txt_input, state],
            outputs=[txt_input, chatbot, graph_out, debug_out, radar_out],
        )
        btn_clear.click(
            fn=lambda: ([], [], _empty_graph, "", _empty_radar),
            outputs=[chatbot, state, graph_out, debug_out, radar_out],
        )

    return demo


# ══════════════════════════════════════════════════════════════════════════════
# CARGA DE EJES EN NODOS NEO4J  (escribe propiedades en DB, no en memoria)
# Se ejecuta al arrancar: si los nodos ya tienen los ejes los sobreescribe
# con los valores actualizados del CSV. Usa la misma conexion que chat_rag.
# ══════════════════════════════════════════════════════════════════════════════
def initialize_axes():
    """
    Escribe los 5 ejes de analisis directamente en los nodos Program/Job/Step
    de Neo4j, leyendo los valores desde los CSV locales.
    Usa la misma conexion neo4j+s:// que el resto del chat (funciona).
    Los datos quedan persistidos en Neo4j permanentemente.
    """
    import csv as _csv

    COBOL_AXES_CSV = r"C:\Users\P017072\idz_workspace\Jenson_Proy\Pry_able\Font\neo4j_csv\program_axes.csv"
    JCL_JOB_CSV    = r"C:\Users\P017072\idz_workspace\Jenson_Proy\Pry_able\JCL\neo4j_csv\job.csv"
    JCL_STEP_CSV   = r"C:\Users\P017072\idz_workspace\Jenson_Proy\Pry_able\JCL\neo4j_csv\step.csv"

    def _si(v):
        try:
            return int(float(v)) if v not in (None, '', 'None') else 0
        except (ValueError, TypeError):
            return 0

    # Un solo UNWIND por tipo de nodo  (una query, sin bucle)
    MERGE_PROG = """
    UNWIND $rows AS row
    MERGE (p:Program {program_id: row.id})
    SET p.technical_debt_score      = row.tds, p.technical_debt_level      = row.tdl,
        p.complexity_score          = row.cs,  p.complexity_level          = row.cl,
        p.business_value_score      = row.bvs, p.business_value_level      = row.bvl,
        p.business_value_rationale  = row.bvr,
        p.security_risk_score       = row.srs, p.security_risk_level       = row.srl,
        p.security_risk_rationale   = row.srr,
        p.processing_time_sec       = row.pt,
        p.processing_time_rationale = row.ptr,
        p.technical_debt_rationale  = row.tdr,
        p.complexity_rationale      = row.cor
    RETURN count(p) AS n
    """
    MERGE_JOB = """
    UNWIND $rows AS row
    MERGE (j:Job {job_id: row.id})
    SET j.technical_debt_score = row.tds, j.technical_debt_level = row.tdl,
        j.complexity_score     = row.cs,  j.complexity_level     = row.cl,
        j.business_value_score = row.bvs, j.business_value_level = row.bvl,
        j.security_risk_score  = row.srs, j.security_risk_level  = row.srl,
        j.processing_time_sec  = row.pt
    RETURN count(j) AS n
    """
    MERGE_STEP = """
    UNWIND $rows AS row
    MERGE (s:Step {step_id: row.id})
    SET s.technical_debt_score = row.tds, s.technical_debt_level = row.tdl,
        s.complexity_score     = row.cs,  s.complexity_level     = row.cl,
        s.business_value_score = row.bvs, s.business_value_level = row.bvl,
        s.security_risk_score  = row.srs, s.security_risk_level  = row.srl,
        s.processing_time_sec  = row.pt
    RETURN count(s) AS n
    """

    def _read_csv(path, id_col):
        rows = []
        try:
            with open(path, encoding='utf-8') as f:
                for r in _csv.DictReader(f):
                    rid = (r.get(id_col) or '').strip()
                    if rid:
                        rows.append({
                            'id':  rid,
                            'tds': _si(r.get('technical_debt_score')),
                            'tdl': r.get('technical_debt_level', ''),
                            'cs':  _si(r.get('complexity_score')),
                            'cl':  r.get('complexity_level', ''),
                            'bvs': _si(r.get('business_value_score')),
                            'bvl': r.get('business_value_level', ''),
                            'bvr': r.get('business_value_rationale', ''),
                            'srs': _si(r.get('security_risk_score')),
                            'srl': r.get('security_risk_level', ''),
                            'srr': r.get('security_risk_rationale', ''),
                            'pt':  _si(r.get('processing_time_sec')),
                            'ptr': r.get('processing_time_rationale', ''),
                            'tdr': r.get('technical_debt_rationale', ''),
                            'cor': r.get('complexity_rationale', ''),
                        })
        except FileNotFoundError:
            print(f"  [init-axes] AVISO: archivo no encontrado: {path}")
        return rows

    print("  [init-axes] Escribiendo ejes en nodos Neo4j...")
    try:
        _drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with _drv.session() as _s:
            # Programs
            prog_rows = _read_csv(COBOL_AXES_CSV, 'program_id')
            if prog_rows:
                # Procesar en lotes de 100 para evitar timeouts
                for i in range(0, len(prog_rows), 100):
                    r = _s.run(MERGE_PROG, rows=prog_rows[i:i+100]).data()
                    n = r[0]['n'] if r else 0
                    print(f"  [init-axes] Program lote {i//100+1}: {n} nodos")
            # Jobs
            job_rows = _read_csv(JCL_JOB_CSV, 'job_id')
            if job_rows:
                r = _s.run(MERGE_JOB, rows=job_rows).data()
                n = r[0]['n'] if r else 0
                print(f"  [init-axes] Job: {n} nodos")
            # Steps en lotes de 100
            step_rows = _read_csv(JCL_STEP_CSV, 'step_id')
            if step_rows:
                for i in range(0, len(step_rows), 100):
                    r = _s.run(MERGE_STEP, rows=step_rows[i:i+100]).data()
                    n = r[0]['n'] if r else 0
                    print(f"  [init-axes] Step lote {i//100+1}: {n} nodos")
        _drv.close()
        print("  [init-axes] OK — ejes escritos en Neo4j.")
    except Exception as _e:
        print(f"  [init-axes] ERROR: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# ARRANQUE
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"OpenAI Chat   : {OPENAI_CHAT_MODEL}")
    print(f"OpenAI Embed  : {OPENAI_EMBED_MODEL}")
    print(f"OpenAI Base   : {OPENAI_BASE_URL}")
    print(f"Neo4j URI     : {NEO4J_URI}")
    print(f"Documentos RAG: {TOP_K} por consulta")
    print()
    initialize_axes()   # Escribe ejes en nodos Neo4j al arrancar
    print()
    app = build_ui()
    app.launch(inbrowser=True, share=True, css=_APP_CSS)

