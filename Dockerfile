FROM node:26-slim AS frontend

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.lock

COPY . .
COPY --from=frontend /app/dashboard/static/dashboard/dist/ /app/dashboard/static/dashboard/dist/
RUN mkdir -p /app/data

EXPOSE 8000

# --insecure lets runserver serve the built UI from static/ while DEBUG is off.

CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py runserver --insecure 0.0.0.0:${PORT}"]
