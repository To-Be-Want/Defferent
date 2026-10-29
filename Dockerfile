FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk ca-certificates libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY requirements-cloud.txt requirements-agent.txt /app/
RUN pip install --no-cache-dir -r requirements-agent.txt
COPY . /app
EXPOSE 7790
CMD ["python", "cloud_app.py", "-p", "7790"]
