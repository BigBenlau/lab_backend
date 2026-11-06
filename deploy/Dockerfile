# 版本標籤固定；正式部署驗收時再記錄平台對應的 image digest。
FROM python:3.14.10-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.lock \
    && groupadd -g 10001 lab && useradd -u 10001 -g lab -M lab
COPY lab_backend ./lab_backend
USER 10001:10001
CMD ["python", "-m", "uvicorn", "lab_backend.application:public_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--limit-concurrency", "8"]
