# Friction log

Submissions with friction logs can earn up to a 10% judging bonus. Add an entry
whenever something takes longer than it should.

| Date | Task attempted | Steps taken | Expected vs. actual | Severity | Workaround | Suggestion |
|------|----------------|-------------|---------------------|----------|------------|------------|
| 2026-10-13 | Start an MCP server from current docs | Installed `mcp`, imported `mcp.server.fastmcp` | Expected FastMCP; latest SDK (2.x) renamed it to `MCPServer` and older tutorials fail on import | Low | Used `mcp.server.mcpserver.MCPServer` | Alexa+ sample code should pin an SDK version or show the 2.x import |
| 2026-10-14 | Integrate and test the server with real Alexa+ | Looked for a way to register an MCP server with Alexa+ and test on a device | Expected the Alexa+ track to provide a path to deploy/test an Alexa+ integration; actual: the Alexa AI CLI, MCP Toolkit and devices are partner-gated, so participants cannot deploy or test against real Alexa+ | High | Building a web simulator that is a real MCP client (track allows a simulated Alexa+ experience) | Give hackathon participants sandbox access to Alexa+ or an official Alexa+ simulator |
| 2026-10-14 | Check tool errors in a benchmark client | Read `result.isError` on a `CallToolResult` from `mcp` 2.3 | Expected the spec's `isError` field name; SDK 2.x models expose snake_case `is_error` and raise `AttributeError` on `isError` | Low | Used `result.is_error` | SDK migration notes should list the camelCase → snake_case attribute rename |
