"""Model Context Protocol integration for Mini-Drop.

The MCP dependency is optional so the core server keeps supporting Python 3.9.
Importing this package never imports the SDK eagerly.
"""

from server.app.mcp_integration.client import (
    MCPClientError,
    MCPClientManager,
    MCPConnectorConfig,
    load_mcp_connector_configs,
)

__all__ = [
    "MCPClientError",
    "MCPClientManager",
    "MCPConnectorConfig",
    "load_mcp_connector_configs",
]
