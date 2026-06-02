# HostIA AWS GraphRAG

Plataforma GraphRAG para análisis de sistemas mainframe COBOL/JCL/mallas/rutinas.

Objetivo:
- Reconstruir en AWS el sistema GraphRAG actual.
- Usar como blueprint el modelo existente en HostIA.
- Separar parsing, grafo, RAG, API y despliegue cloud-native.

Capas:
1. Ingesta de fuentes mainframe.
2. Parsing COBOL, JCL, mallas y rutinas.
3. Generación de nodos y relaciones.
4. Cálculo determinístico de 5 ejes.
5. Generación de documentos RAG.
6. Carga a grafo.
7. Búsqueda vectorial.
8. Chat GraphRAG.
9. Matriz de decisión estratégica.