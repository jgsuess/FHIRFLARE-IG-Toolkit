FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY services.py .
COPY forms.py .
COPY templates/ templates/
COPY static/ static/
COPY tests/ tests/

# Ensure /tmp is writable as a fallback
RUN mkdir -p /tmp && chmod 777 /tmp

EXPOSE 5000
ENV FLASK_APP=app.py
ENV FLASK_ENV=development
CMD ["flask", "run", "--host=0.0.0.0"]