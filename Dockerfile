FROM python:3.11-slim

WORKDIR /app

COPY . /app

ENV PYTHONUNBUFFERED=1
ENV PORTFOLIO_GUARD_LIVE_DATA=1

EXPOSE 8765

CMD ["sh", "-c", "python app.py --host 0.0.0.0 --port ${PORT:-8765}"]
