# Fund Investment Assistant - Backend Server

这是基金投资助手项目的 **Python 后端服务**。它使用 FastAPI, SQLAlchemy 和 Typer 构建，负责所有核心业务逻辑、数据处理和 API 服务。

该项目以**持有份额**为核心，能够动态追踪资产价值，提供实时的盘中估算。

## ✨ 主要功能

-   **份额核心制**: 用户通过输入买入金额来建立持仓，系统会自动根据当日净值计算并记录**持有份额**。后续所有资产价值均基于此份额动态计算。
-   **动态资产追踪**:
    -   **每日校准**: 每日自动获取最新基金净值，并用 `份额 × 最新净值` 的方式更新您的**持有金额**，确保其反映真实资产价值。
    -   **盘中估算**: 在交易时段内，定时获取实时估值，动态计算并更新**预估金额**、**预估涨跌幅**和**估值更新时间**。
-   **RESTful API**: 提供一套完整的 API，用于持仓管理（增删改查）和带有灵活均线选项的历史数据查询。
-   **命令行工具 (CLI)**: 提供了一套功能对等的管理命令，方便在服务器端进行数据导入导出、手动同步、持仓管理等所有操作。
-   **数据导入/导出**: 支持通过 JSON 文件备份和恢复核心的持仓数据（基金代码和份额）。
-   **数据库支持**: 使用 PostgreSQL，并支持自定义 Schema 进行数据隔离。

## 🛠️ 技术栈

-   **Web 框架**: FastAPI
-   **命令行框架**: Typer
-   **数据库 ORM**: SQLAlchemy
-   **数据库**: PostgreSQL
-   **项目管理**: uv (替代 pip 和 venv)
-   **定时任务**: Schedule
-   **HTTP 客户端**: httpx

## 🚀 本地开发环境设置

### 1. 环境准备

-   **安装 uv**:
    ```bash
    # macOS / Linux
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```
-   **安装 PostgreSQL**: 确保本地已安装并运行 PostgreSQL 服务。
-   **创建数据库**:
    ```sql
    CREATE DATABASE fund_assistant;
    ```

### 2. 项目配置

-   **创建 `.env` 文件**: 在项目根目录下，创建一个名为 `.env` 的文件，并填入您的本地数据库配置。
    ```dotenv
    # .env
    DATABASE_URL="postgresql://your_user:your_password@localhost:5432/fund_assistant"
    DB_SCHEMA="fund_app"
    ```

### 3. 安装与运行

-   **创建并激活虚拟环境**:
    ```bash
    uv venv
    source .venv/bin/activate
    ```
-   **安装项目依赖**:
    ```bash
    uv sync
    ```
-   **启动 API 及定时任务服务**:
    ```bash
    uvicorn src.python_cli_starter.main:api_app --reload
    ```
    服务启动后，定时任务会自动在后台运行。您可以在 `http://127.0.0.1:8888/docs` 查看 API 文档。

---

## ⌨️ 命令行 (CLI) 用法

所有命令都通过 `uv run cli` 执行，无需手动激活虚拟环境。

-   **查看所有可用命令**:
    ```bash
    uv run cli --help
    ```

### 查看持仓

以美观的表格形式列出所有持仓基金及其预估盈亏。
```bash
uv run cli list-holdings
```

### 添加新的持仓
当您添加一笔投资时，系统会根据您输入的金额和当时的基金净值，自动计算出您持有的**份额**。
```bash
# 示例：投资 5000 元到一只基金
uv run cli add-holding --code "161725" --amount 5000
```
-   `--code` / `-c`: 基金代码 (**必填**)
-   `--amount` / `-a`: **买入**金额 (**必填**)
-   `--name` / `-n`: 基金名称 (可选, 程序会自动获取)

### 更新持仓金额
此操作会调整您的总资产至新指定的金额，并根据最新的基金净值**重新计算您的总份额**。
```bash
# 示例：将代码为 161725 的基金总资产调整为 6500 元
uv run cli update-holding --code "161725" --amount 6500
```
-   `--code` / `-c`: 要更新的基金代码 (**必填**)
-   `--amount` / `-a`: **新的总**持有金额 (**必填**)

### 删除持仓记录
此操作会进行交互式确认，防止误删。
```bash
# 示例：删除代码为 161725 的基金
uv run cli delete-holding 161725
```
> 要跳过确认，可添加 `--force` 或 `-f` 标志。

### 手动同步历史数据
立即触发一次所有持仓基金的历史净值同步任务，并根据最新净值校准持仓金额。
```bash
uv run cli sync-history
```

### 导入/导出数据
备份和恢复核心的持仓数据（代码和份额）。
```bash
# 导出所有持仓到 a_backup.json 文件
uv run cli export-data -o a_backup.json

# 从 a_backup.json 文件增量导入数据
uv run cli import-data a_backup.json

# 覆盖式导入（会先删除所有旧数据）
uv run cli import-data a_backup.json --overwrite
```

---

## 🐳 生产环境 Docker 部署

我们使用 Docker 和 Docker Compose 进行生产环境的部署。部署流程分为**构建镜像**和**运行容器**两个步骤。

### 1. 环境准备

-   **安装 Docker 和 Docker Compose**。
-   **准备外部 Docker 网络**: 如果网络不存在，请先创建它：
    ```bash
    docker network create shared-db-network
    ```
-   **准备生产环境变量文件**: 创建 `.env.prod` 文件。
    ```dotenv
    # .env.prod
    # 注意：DATABASE_URL 中的主机名应为数据库容器在 Docker 网络中的服务名
    DATABASE_URL="postgresql://prod_user:prod_password@postgres_container_name:5432/prod_db"
    DB_SCHEMA="fund_production"
    ```

### 2. 构建 Docker 镜像 (打包)

```bash
docker build -t fund-strategies-service:latest .
```

### 3. 运行服务 (Docker Compose)

启动服务：
```bash
docker compose up -d
```

### 4. 常用 Docker 命令

-   **查看服务日志**:
    ```bash
    docker compose logs -f fund-strategies-service
    ```
-   **停止并移除容器**:
    ```bash
    docker compose down
    ```
-   **在运行的容器中执行 CLI 命令**:
    ```bash
    docker compose exec fund-strategies-service cli sync-history
    ```

---

## 📄 License

MIT