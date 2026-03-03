# =========================================================================
#  Stage 1: Builder - 负责编译和安装所有依赖
# =========================================================================
FROM docker.m.daocloud.io/python:3.12-slim-bookworm AS builder

# 设置环境变量，提升Docker内Python应用的性能和稳定性
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    # 告诉 uv 在构建时不要使用缓存，保持CI/CD环境纯净
    UV_NO_CACHE=1

# --- 使用您指定的 echo 命令来设置清华镜像源 ---
# 这会创建一个新的源配置文件，覆盖默认设置
RUN echo "Types: deb" > /etc/apt/sources.list.d/debian.sources && \
    echo "URIs: http://mirrors.tuna.tsinghua.edu.cn/debian" >> /etc/apt/sources.list.d/debian.sources && \
    echo "Suites: bookworm bookworm-updates bookworm-backports" >> /etc/apt/sources.list.d/debian.sources && \
    echo "Components: main" >> /etc/apt/sources.list.d/debian.sources

# 安装系统级依赖 (如 tzdata 用于时区)
RUN apt-get update && apt-get install -y tzdata && \
    ln -fs /usr/share/zoneinfo/${TZ} /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata && \
    # 清理 apt 缓存，减小镜像体积
    rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 使用 pip 安装 uv (使用国内源加速)
RUN pip install --no-cache-dir --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir uv -i https://pypi.tuna.tsinghua.edu.cn/simple

# 利用 Docker 层缓存：先复制配置文件并安装项目所有依赖
# 只有当 pyproject.toml 文件变化时，这一层才会重新执行
COPY ./pyproject.toml ./README.md ./
COPY ./src ./src
# --system 参数将依赖安装到系统 Python 环境中，适合在容器中使用
# -i 参数指定 PyPI 镜像源
RUN uv pip install --system --no-cache . -i https://pypi.tuna.tsinghua.edu.cn/simple

# =========================================================================
#  Stage 2: Final Image - 最终的生产镜像
# =========================================================================
FROM docker.m.daocloud.io/python:3.12-slim-bookworm AS final

# 设置与 builder 阶段相同的环境变量，并指定 Playwright 浏览器缓存路径到全局目录
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 从 builder 阶段复制已配置好的时区信息和国内 apt 镜像源(加速最终阶段的 apt 安装)
COPY --from=builder /etc/localtime /etc/localtime
COPY --from=builder /usr/share/zoneinfo/${TZ} /usr/share/zoneinfo/${TZ}
COPY --from=builder /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list.d/debian.sources

# 从 builder 阶段先复制已安装的所有 Python 依赖包和可执行文件
# 这样在 final 阶段就能直接调用 playwright 命令了
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 此时我们还是 root 用户。更新 apt 并安装 Playwright 所需的系统级依赖及 Chromium 内核
# 安装完成后清理缓存，并将浏览器目录的权限赋予所有人读取/执行
RUN apt-get update && \
    playwright install chromium && \
    playwright install-deps chromium && \
    rm -rf /var/lib/apt/lists/* && \
    chmod -R 755 /ms-playwright

# 创建一个非 root 用户来运行应用，这是安全最佳实践
# 避免在容器内使用 root 用户
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser

# 设置新的工作目录
WORKDIR /home/appuser/app

# 复制应用源代码，并确保文件所有者是我们的非 root 用户
COPY --chown=appuser:appgroup ./src ./src

# 声明容器将监听的端口（仅用于文档目的，实际端口映射在 docker-compose.yml 中定义）
EXPOSE 8888

# 不设置固定的 CMD 或 ENTRYPOINT，启动命令将由 docker-compose.yml 提供
# 这使得同一个镜像可以用于不同的目的（例如，运行API服务或执行一次性CLI命令）