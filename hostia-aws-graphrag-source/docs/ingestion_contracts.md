# Contratos De Ingesta

## Nodos

### Program
Campos:
- program_id
- name
- loc
- author
- date_written
- app_name
- description
- technical_debt_score
- technical_debt_level
- complexity_score
- complexity_level
- business_value_score
- business_value_level
- security_risk_score
- security_risk_level
- processing_time_sec

### Job
Campos:
- job_id
- job_name
- step_count
- technical_debt_score
- complexity_score
- business_value_score
- security_risk_score
- processing_time_sec

### Routine
Campos:
- routine_id
- name
- loc
- technical_debt_score
- complexity_score
- business_value_score
- security_score
- processing_time_score

### MallaObject
Campos:
- object_id
- malla_id
- memname
- table
- group
- typ
- description
- source_file

## Relaciones

Cada relación debe tener:
- source_id
- target_id
- relation_type
- source_file
- confidence opcional
- metadata opcional