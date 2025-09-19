# Seguridad

- Protege `/provision/*`, `/events/*`, `/by-label`, `/gc`, `/scale/*`, `DELETE /containers/*` con API key.
- No expongas `docker:dind` en redes no confiables.
- Si usas panel en otro origen, habilita CORS solo para orígenes permitidos.
- Registra y rota `orchestrator.db`. Contiene URLs locales de engines.
- Opcional: separar `READ_API_KEY` para métodos GET sensibles.
