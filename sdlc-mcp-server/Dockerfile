FROM python:3.12-slim

# No heavy system deps — this server only makes outbound HTTP calls (Jira/GitHub/Slack).
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8100

CMD ["python", "server.py"]
