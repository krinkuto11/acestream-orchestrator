# Panel

Ruta: `/panel`.

Funciones:
- KPI de engines y streams.
- Lista de engines y streams activos.
- Detalle de un stream con gráfica de `down/up/peers` en la última hora.
- Botones: **Stop stream** (llama `command_url?method=stop` directamente al engine) y **Delete engine** (DELETE al orquestador).

Parámetros:
- Caja `orch`: base URL del orquestador.
- Caja `API key`: Bearer para endpoints protegidos.
- Intervalo de refresco: 2–30 s.

CORS:
- El panel se sirve desde el mismo host. Si lo separas, habilita CORS en `main.py`.
