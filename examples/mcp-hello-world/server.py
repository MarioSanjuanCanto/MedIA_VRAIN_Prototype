import sys
from mcp.server.fastmcp import FastMCP

# Inicializar servidor MCP accesible desde contenedores Docker
mcp = FastMCP(
    "hello-world-server",
    host="0.0.0.0",
    port=8000,
)


@mcp.tool()
def hello_world(name: str = "Mundo") -> str:
    """Devuelve un saludo de bienvenida personalizado.

    Args:
        name: Nombre de la persona o entidad a saludar (por defecto 'Mundo').
    """
    return f"¡Hola, {name}! Saludos desde el servidor MCP de Watchity."


@mcp.tool()
def get_server_info() -> dict:
    """Devuelve el estado e información básica del servidor MCP."""
    return {
        "status": "online",
        "service": "hello-world-server",
        "python_version": sys.version.split()[0],
        "message": "Servidor MCP Hello World funcionando correctamente.",
    }


if __name__ == "__main__":
    # Expone el servidor en http://0.0.0.0:8000/mcp
    mcp.run(transport="streamable-http")
