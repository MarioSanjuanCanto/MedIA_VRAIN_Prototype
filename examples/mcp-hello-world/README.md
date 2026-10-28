# MCP Hello World

Servidor MCP mínimo para comprobar que Hermes puede descubrir y ejecutar una
herramienta. Expone `hello_world` y `get_server_info` en el puerto 8000.

## Preparar y arrancar

```bash
uv venv
uv pip install "mcp>=1.28,<2"
uv run python server.py
```

Endpoint: http://127.0.0.1:8000/mcp

Para registrarlo en Hermes local:

```bash
hermes mcp add hello-world --url http://127.0.0.1:8000/mcp
hermes mcp test hello-world
```

Si Hermes está en Docker, usa
`http://host.docker.internal:8000/mcp`. Este ejemplo es independiente de la
aplicación principal.
