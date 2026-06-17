FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY examples/config.json ./examples/config.json
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

EXPOSE 8765

CMD ["vn-event-dw", "serve-api", "--db", "/app/data/warehouse.db", "--host", "0.0.0.0", "--port", "8765"]
