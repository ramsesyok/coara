"""coara-mcp ASGI サーバの起動エントリポイント。"""

# TODO: MCP サーバ（HTTP + SSE）を起動する
# 実装方針（docs/detailed_designed_coara-mcp.md 参照）:
#   1. config.py で coara-mcp.yaml をロード
#   2. MCP Python SDK / FastMCP で MCPServer を構築
#   3. tools/ 配下の各ツールを登録（query, search, get_snippet, ...）
#   4. adapter/ でパス互換（/mcp/sse, /mcp/request）を設定
#   5. uvicorn で ASGI アプリを起動


def main() -> None:
    raise NotImplementedError("TODO: implement coara-mcp server startup")


if __name__ == "__main__":
    main()
