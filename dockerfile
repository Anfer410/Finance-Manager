FROM python:3.13-slim AS deps
WORKDIR /build
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM deps AS app
WORKDIR /app
COPY app /app
ENV APP_ENV=prod
ENTRYPOINT ["python", "main.py"]
