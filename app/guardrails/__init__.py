"""Guardrails del servicio de estimación (defense in depth).

| Capa | Tipo                  | Dónde                                         |
|------|-----------------------|-----------------------------------------------|
| 1    | Sintáctica (input)    | app/schemas.py (EstimationRequest) + router   |
| 2    | Semántica (input)     | moderation.py, injection.py, pii.py           |
| 3    | Robustez del prompt   | app/prompts (scope, delimitadores)            |
| 4    | Sintáctica (output)   | app/schemas.py vía Instructor                 |
| 5    | Semántica (output)    | validadores de EstimationResult + pii.py      |

La política y el setting de modo de cada guardrail están en base.GUARDRAILS.
"""
