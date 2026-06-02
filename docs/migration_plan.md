# Plan De Construcción AWS

## Fase 1 - Blueprint

- Documentar modelo de grafo.
- Documentar reglas de 5 ejes.
- Documentar matriz estratégica.
- Documentar contratos de ingesta.

## Fase 2 - Ingesta

- Crear estructura S3 RAW.
- Crear estructura S3 CURATED.
- Adaptar parsers para leer desde S3 o filesystem.

## Fase 3 - Grafo

- Generar nodos y relaciones normalizados.
- Crear loader para grafo.
- Validar relaciones principales.

## Fase 4 - RAG

- Generar documentos JSONL.
- Generar embeddings.
- Cargar vector store.

## Fase 5 - API

- Crear servicio GraphRAG.
- Separar consultas de grafo, búsqueda vectorial y llamada LLM.

## Fase 6 - UI

- Crear interfaz web.
- Reutilizar visualmente la idea de chat_rag_neural.py.

## Fase 7 - Seguridad

- Mover secretos a Secrets Manager.
- Eliminar claves del código.
- Activar logs y auditoría.