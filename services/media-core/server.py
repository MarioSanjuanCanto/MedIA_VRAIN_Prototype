"""Executable entry point for the Media Core MCP server."""

import sys
import os
import subprocess
from pathlib import Path

# Resolve the project interpreter before importing third-party dependencies.
# This also handles `python server.py` from a terminal with no active venv.
def ensure_project_python():
    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if executable.is_file() and Path(sys.prefix).resolve() != environment.resolve():
        if os.environ.get("MEDIA_CORE_REEXEC") == str(environment):
            raise RuntimeError("No se pudo activar el .venv de media-core; ejecuta uv sync.")
        env = dict(os.environ, MEDIA_CORE_REEXEC=str(environment))
        print(f"[Media Core] Usando Python del proyecto: {executable}", file=sys.stderr, flush=True)
        command = [str(executable), str(root / "server.py"), *sys.argv[1:]]
        if os.name == "nt":
            # Windows execve releases the original console process. Wait explicitly
            # and inherit its console so startup errors and Ctrl+C stay visible.
            try:
                returncode = subprocess.call(command, env=env)
            except KeyboardInterrupt:
                returncode = 130
            raise SystemExit(returncode)
        os.execve(str(executable), command, env)


if __name__ == "__main__":
    ensure_project_python()

import threading

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from media_core.config import (
    CHROMA_AUTO_INDEX,
    SERVER_HOST,
    SERVER_NAME,
    SERVER_PORT,
    SERVER_TRANSPORT,
)
from media_core.indexing import index_all_test_vectors
from media_core.services.chroma import get_chroma_store
from media_core.tools import register_tools

mcp = FastMCP(
    SERVER_NAME,
    host=SERVER_HOST,
    port=SERVER_PORT,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1:*",
            "localhost:*",
            "host.docker.internal:*",
        ],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://host.docker.internal:*",
        ],
    ),
)
register_tools(mcp)


def start_server():
    try:
        get_chroma_store().embedder.warm_up()
        print("[Embeddings] Modelo cargado antes de aceptar peticiones.", flush=True)
    except Exception as error:
        print(
            f"[Embeddings Startup Warning] {error}. Se usará búsqueda léxica si falla la búsqueda semántica.",
            file=sys.stderr,
            flush=True,
        )

    def warm_index():
        try:
            report = index_all_test_vectors()
            print(
                "[Chroma] "
                f"status={report['status']} chunks={report['indexed_chunks']} "
                f"videos={report['videos_processed']}"
            )
        except Exception as error:
            print(
                f"[Chroma Startup Warning] {error}. Se usará búsqueda léxica.",
                file=sys.stderr,
            )
    if CHROMA_AUTO_INDEX:
        threading.Thread(target=warm_index, daemon=True, name="media-index-warmup").start()
    mcp.run(transport=SERVER_TRANSPORT)


if __name__ == "__main__":
    start_server()
