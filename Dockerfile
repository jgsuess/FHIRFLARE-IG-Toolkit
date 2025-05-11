FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY static/ static/
COPY instance/ instance/
COPY config.py .
COPY .env .
EXPOSE 5009
CMD ["flask", "run", "--host=0.0.0.0", "--port=5009"]