from mcp.server import MCPServer
from mcp_types import ToolAnnotations


server = MCPServer("cyberclaw-demo")


@server.tool(annotations=ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    open_world_hint=False,
))
def echo(message: str) -> str:
    """Return the supplied message without changing external state."""
    return message


@server.tool(annotations=ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    open_world_hint=False,
))
def remember_demo(note: str) -> str:
    """Demonstrate a state-changing MCP tool without writing to disk."""
    return f"remembered: {note}"


if __name__ == "__main__":
    server.run(transport="stdio")
