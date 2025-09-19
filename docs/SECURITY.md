# Security

- Protect `/provision/*`, `/events/*`, `/by-label`, `/gc`, `/scale/*`, `DELETE /containers/*` with API key.
- Don't expose `docker:dind` on untrusted networks.
- If using panel from another origin, enable CORS only for allowed origins.
- Log and rotate `orchestrator.db`. Contains local engine URLs.
- Optional: separate `READ_API_KEY` for sensitive GET methods.
