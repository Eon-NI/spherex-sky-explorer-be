FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY eon_common ./eon_common
COPY services ./services
COPY manage.py ./
COPY docker/init_s3.py ./docker/init_s3.py
RUN pip install --no-cache-dir .
RUN useradd --create-home eon && install -d -o eon -g eon /home/eon/beat
USER eon
CMD ["gunicorn", "eon_common.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
