"""coara-embed gRPC サーバの起動エントリポイント。"""

# TODO: gRPC サーバを起動する
# 実装順序（詳細設計 §15 参照）:
#   1. config.py で coara-embed.yaml をロード
#   2. registry/profiles.py で profiles.yaml をロード・検証
#   3. metadb/engine.py で SQLite 接続・Alembic マイグレーション
#   4. rpc/service.py の CoaraEmbedServicer をサーバに登録
#   5. grpc.server() を生成して listen し、シグナルハンドラで graceful stop


def main() -> None:
    raise NotImplementedError("TODO: implement coara-embed gRPC server startup")


if __name__ == "__main__":
    main()
