# 版本標籤固定；正式部署驗收時再記錄平台對應的 image digest。
FROM python:3.14.10-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.lock \
    && groupadd -g 10001 lab && useradd -u 10001 -g lab -M lab \
    && install -d -m 0700 -o 10001 -g 10001 /data
COPY lab_backend ./lab_backend
# 只加入維護程式，不複製私人參考、主機資料或公開前端。
COPY scripts/maintenance.py scripts/prepare_initial_data.py ./scripts/
# COPY會沿用來源模式；統一程式權限，避免0600檔案／0700目錄使非root帳戶無法讀取。
# 只處理映像內的程式，不改/data、掛載來源或憑證的權限。
RUN find /app -type d -exec chmod 0755 {} + \
    && find /app -type f -exec chmod 0644 {} +
# 空 volume 首次掛載繼承 /data 擁有者；初始化仍由明確 CLI 操作完成。
USER 10001:10001
CMD ["python", "-m", "uvicorn", "lab_backend.application:public_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--limit-concurrency", "8"]
