NODE_TYPES = [
    "Program",
    "Job",
    "Step",
    "Routine",
    "Dataset",
    "JclDataset",
    "DB2Table",
    "Copybook",
    "EmbeddedLayout",
    "MallaObject",
    "MallaCondition",
    "RagDocument",
]

RELATION_TYPES = [
    "HAS_STEP",
    "EXECUTES",
    "READS",
    "WRITES",
    "CALLS",
    "ACCESSES_DB2",
    "READS_DATASET",
    "WRITES_DATASET",
    "USES_COPYBOOK",
    "HAS_LAYOUT",
    "REQUIRES_CONDITION",
    "PRODUCES_CONDITION",
    "DEPENDS_ON",
    "PRECEDES",
    "SCHEDULES_JOB",
    "DOCUMENTS",
]

AXES = [
    "technical_debt",
    "complexity",
    "business_value",
    "security_risk",
    "processing_time",
]