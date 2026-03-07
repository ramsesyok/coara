# 開発環境セットアップガイド

## 前提条件

| ツール | 推奨バージョン |
|---|---|
| Go | 1.22 以上 |
| Python | 3.11 以上 |
| protoc | 27.x 以上 |

---

## 1. protoc（Protocol Buffers コンパイラ）のインストール

### Linux（apt）

```bash
# 最新版は GitHub Releases から取得することを推奨
PB_REL="https://github.com/protocolbuffers/protobuf/releases"
PB_VERSION="27.3"

curl -LO "${PB_REL}/download/v${PB_VERSION}/protoc-${PB_VERSION}-linux-x86_64.zip"
unzip protoc-${PB_VERSION}-linux-x86_64.zip -d $HOME/.local
# PATH に $HOME/.local/bin が含まれていることを確認
export PATH="$HOME/.local/bin:$PATH"

protoc --version   # libprotoc 27.x
```

### macOS（Homebrew）

```bash
brew install protobuf
protoc --version
```

### Windows（WSL2 の場合は Linux 手順を使用）

---

## 2. Go 側のセットアップ

### 2-1. Go プラグインのインストール

```bash
# gRPC コード生成プラグイン
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest

# PATH に追加（~/.bashrc や ~/.zshrc へ記載）
export PATH="$PATH:$(go env GOPATH)/bin"
```

### 2-2. Go 依存関係のインストール

```bash
go mod tidy
```

### 2-3. proto から Go コードを生成

```bash
# リポジトリルートで実行
protoc \
  --proto_path=proto \
  --go_out=gen/go \
  --go_opt=paths=source_relative \
  --go-grpc_out=gen/go \
  --go-grpc_opt=paths=source_relative \
  proto/coara/embed/v1/coara_embed.proto
```

生成先: `gen/go/coara/embed/v1/`

| ファイル | 内容 |
|---|---|
| `coara_embed.pb.go` | メッセージ型 |
| `coara_embed_grpc.pb.go` | サービス stub / interface |

### 2-4. ビルドと実行確認

```bash
go build ./...
go run ./cmd/coara-cli/
```

---

## 3. Python 側のセットアップ

### 3-1. 仮想環境の作成

```bash
# coara-embed
cd services/coara-embed
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 開発用依存含めてインストール
pip install -e ".[dev]"
```

```bash
# coara-mcp（別ターミナル）
cd services/coara-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 3-2. proto から Python コードを生成

`grpcio-tools` に protoc バンドル版が含まれるため、別途 protoc をインストールしなくても生成できる。

```bash
# coara-embed の仮想環境を有効にした状態で実行
cd /path/to/coara   # リポジトリルート

python -m grpc_tools.protoc \
  --proto_path=proto \
  --python_out=services/coara-embed/app/rpc \
  --grpc_python_out=services/coara-embed/app/rpc \
  proto/coara/embed/v1/coara_embed.proto

# coara-mcp 側（gRPC クライアントとして使う）
python -m grpc_tools.protoc \
  --proto_path=proto \
  --python_out=services/coara-mcp/app/embed_client \
  --grpc_python_out=services/coara-mcp/app/embed_client \
  proto/coara/embed/v1/coara_embed.proto
```

生成先の例（coara-embed）: `services/coara-embed/app/rpc/`

| ファイル | 内容 |
|---|---|
| `coara_embed_pb2.py` | メッセージクラス |
| `coara_embed_pb2_grpc.py` | Stub / Servicer クラス |

> **注意**: 生成ファイルの import パスが `coara.embed.v1` ではなく相対パスになる場合がある。
> `coara_embed_pb2_grpc.py` 内の `import coara_embed_pb2 as ...` を適宜修正するか、
> `--python_out` に `pyi_out` オプションを追加して型スタブも生成する。

### 3-3. テストの実行

```bash
# coara-embed
cd services/coara-embed
pytest tests/

# coara-mcp
cd services/coara-mcp
pytest tests/
```

---

## 4. コード生成のまとめスクリプト（任意）

```bash
#!/usr/bin/env bash
# scripts/gen_proto.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROTO_SRC="${REPO_ROOT}/proto"

echo "=== Go ==="
protoc \
  --proto_path="${PROTO_SRC}" \
  --go_out="${REPO_ROOT}/gen/go" \
  --go_opt=paths=source_relative \
  --go-grpc_out="${REPO_ROOT}/gen/go" \
  --go-grpc_opt=paths=source_relative \
  "${PROTO_SRC}/coara/embed/v1/coara_embed.proto"

echo "=== Python (coara-embed) ==="
python -m grpc_tools.protoc \
  --proto_path="${PROTO_SRC}" \
  --python_out="${REPO_ROOT}/services/coara-embed/app/rpc" \
  --grpc_python_out="${REPO_ROOT}/services/coara-embed/app/rpc" \
  "${PROTO_SRC}/coara/embed/v1/coara_embed.proto"

echo "=== Python (coara-mcp) ==="
python -m grpc_tools.protoc \
  --proto_path="${PROTO_SRC}" \
  --python_out="${REPO_ROOT}/services/coara-mcp/app/embed_client" \
  --grpc_python_out="${REPO_ROOT}/services/coara-mcp/app/embed_client" \
  "${PROTO_SRC}/coara/embed/v1/coara_embed.proto"

echo "Done."
```

```bash
chmod +x scripts/gen_proto.sh
./scripts/gen_proto.sh
```

---

## 5. ディレクトリ構成（生成ファイルの配置先）

```
coara/
  gen/
    go/
      coara/embed/v1/
        coara_embed.pb.go
        coara_embed_grpc.pb.go
  services/
    coara-embed/
      app/
        rpc/
          coara_embed_pb2.py
          coara_embed_pb2_grpc.py
    coara-mcp/
      app/
        embed_client/
          coara_embed_pb2.py
          coara_embed_pb2_grpc.py
```

> `gen/` および `*_pb2.py` は生成物のため `.gitignore` に追加することを推奨。

---

## 6. .gitignore への追加推奨項目

```gitignore
# 生成コード
gen/
*_pb2.py
*_pb2_grpc.py

# Python
**/.venv/
**/__pycache__/
*.pyc
*.egg-info/

# Go
*.test
/bin/

# データ
data/

# SQLite
*.sqlite3
```
