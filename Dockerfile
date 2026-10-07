FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GEOSPATIAL_DB=/data/geospatial.sqlite3
WORKDIR /service
COPY requirements.lock pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-deps . \
    && useradd --create-home api && mkdir /data && chown api:api /data
USER api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
