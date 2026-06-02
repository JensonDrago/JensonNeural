# Modelo De Grafo

## Nodos

- Program
- Job
- Step
- Routine
- Dataset
- JclDataset
- DB2Table
- Copybook
- EmbeddedLayout
- MallaObject
- MallaCondition
- RagDocument

## Relaciones

- (Job)-[:HAS_STEP]->(Step)
- (Step)-[:EXECUTES]->(Program)
- (Step)-[:READS]->(JclDataset)
- (Step)-[:WRITES]->(JclDataset)
- (Program)-[:CALLS]->(Routine)
- (Routine)-[:CALLS]->(Routine)
- (Program)-[:ACCESSES_DB2]->(DB2Table)
- (Routine)-[:ACCESSES_DB2]->(DB2Table)
- (Program)-[:READS_DATASET]->(Dataset)
- (Program)-[:WRITES_DATASET]->(Dataset)
- (Program)-[:USES_COPYBOOK]->(Copybook)
- (Program)-[:HAS_LAYOUT]->(EmbeddedLayout)
- (MallaObject)-[:REQUIRES_CONDITION]->(MallaCondition)
- (MallaObject)-[:PRODUCES_CONDITION]->(MallaCondition)
- (MallaObject)-[:DEPENDS_ON]->(MallaObject)
- (MallaObject)-[:PRECEDES]->(MallaObject)
- (MallaObject)-[:SCHEDULES_JOB]->(Job)
- (RagDocument)-[:DOCUMENTS]->(Program|Job|Step|Routine|MallaObject|Dataset|DB2Table)

## Principios

- El grafo debe permitir impacto, lineage, dependencias y análisis estratégico.
- Los IDs deben ser estables.
- Los scores de ejes deben ser determinísticos.
- Las relaciones deben conservar evidencia de origen cuando aplique.