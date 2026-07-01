"""
backend/mcp/connectors/__init__.py

Imports all connector files so their MCPRegistry.register() calls run at startup.

When MCPRegistry.__init__() does `import backend.mcp.connectors`, this file is
executed — which imports each connector module — which triggers their register() calls.

Adding a new connector: create the connector file and add an import here.
The registry itself never needs to change.
"""
# These imports trigger the self-registration calls at the bottom of each file.
import backend.mcp.connectors.jira_connector   # noqa: F401
import backend.mcp.connectors.github_connector  # noqa: F401
import backend.mcp.connectors.slack_connector   # noqa: F401
# teams + drive connectors are DISABLED in config/mcp_registry.yaml — uncomment
# these imports (and the yaml `enabled: true`) to bring them back.
# import backend.mcp.connectors.teams_connector   # noqa: F401
# import backend.mcp.connectors.drive_connector   # noqa: F401
import backend.mcp.connectors.confluence_connector  # noqa: F401
