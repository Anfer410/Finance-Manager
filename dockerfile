FROM python:3.13 AS build
COPY app/requirements.txt .
RUN pip install -r requirements.txt

FROM build AS app
WORKDIR /app
COPY app /app
ENTRYPOINT [ "python", "main.py" ]