# syntax=docker/dockerfile:1
#
# multi-agent-lite 运行镜像。
#
# 设计取舍：
# - 只装 runtime 依赖（requirements.txt），pytest / ruff / mypy 不进镜像；
# - 非 root 运行（uid 10001）；
# - 迁移由 entrypoint 执行（alembic upgrade head），应用侧 AUTO_APPLY_SCHEMA=false，
#   让「改表只能走迁移」这条约束在容器里也成立；
# - HEALTHCHECK 打 /ready（含数据库可用性），而不是只查进程的 /health。

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖再拷代码：requirements 不变时改代码可以命中缓存层
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).status == 200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]