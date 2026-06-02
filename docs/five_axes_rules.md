# Reglas De Los 5 Ejes

## Ejes

1. Deuda Técnica
2. Complejidad / Dependencias
3. Valor de Negocio
4. Riesgo de Seguridad
5. Consumo / Tiempo de Proceso Mainframe

## Niveles Programas/JCL

- ALTO: >= 75
- MEDIO: >= 50
- BAJO: >= 25
- MUY_BAJO: < 25

## Niveles Rutinas

- ALTO: >= 75
- MEDIO: >= 45
- BAJO: < 45

## Principio

Los ejes no son generados por IA.
Son calculados por reglas determinísticas sobre:
- LOC
- DB2
- datasets
- rutinas llamadas
- copybooks
- layouts
- GO TO
- SQL DML
- SQLCODE
- TSO
- utilitarios
- condiciones de malla
- criticidad funcional