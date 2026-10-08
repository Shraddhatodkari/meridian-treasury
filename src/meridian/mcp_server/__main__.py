import argparse

from meridian.mcp_server.server import mcp

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Meridian MCP server")
    ap.add_argument("--http", type=int, default=None, help="serve streamable HTTP on this port (default: stdio)")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    if args.http:
        mcp.run("streamable-http", host=args.host, port=args.http)
    else:
        mcp.run("stdio")
