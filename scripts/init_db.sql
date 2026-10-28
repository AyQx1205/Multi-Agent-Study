-- 创建应用专用角色和数据库（用 postgres 超级用户执行一次即可）
--
--   & "D:\postgreSQL\bin\psql.exe" -U postgres -h 127.0.0.1 -f scripts\init_db.sql
--
-- 执行时会提示输入安装 PostgreSQL 时为 postgres 设置的密码。
-- 如果要把密码改成别的，记得同步修改 .env 里的 DATABASE_URL。

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'multi_agent') THEN
        CREATE ROLE multi_agent LOGIN PASSWORD 'multi_agent_dev';
    ELSE
        ALTER ROLE multi_agent WITH LOGIN PASSWORD 'multi_agent_dev';
    END IF;
END
$$;

SELECT 'CREATE DATABASE multi_agent OWNER multi_agent'
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = 'multi_agent'
)
\gexec

-- 如果数据库是手动创建的，把 owner / schema 权限补上，应用才能建表
ALTER DATABASE multi_agent OWNER TO multi_agent;
\connect multi_agent
GRANT ALL ON SCHEMA public TO multi_agent;