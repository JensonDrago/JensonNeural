# Arquitectura AWS

## Capas

1. S3 RAW
   Fuentes COBOL, JCL, mallas y rutinas.

2. S3 CURATED
   CSV/JSONL normalizados.

3. Parsers
   Contenedores Python ejecutados en ECS/Fargate o AWS Batch.

4. Grafo
   Neptune o Neo4j Aura en AWS.

5. Vector Store
   Servicio separado para embeddings RAG.

6. API GraphRAG
   Backend FastAPI.

7. UI
   Gradio temporal o frontend dedicado.

8. Seguridad
   Secrets Manager, IAM, VPC, KMS, CloudWatch.

## Principio

No usar rutas locales.
No guardar secretos en código.
Toda configuración debe venir de variables de entorno.