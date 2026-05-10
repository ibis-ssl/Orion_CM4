FROM python:3.12-slim

WORKDIR /app

COPY server.py /app/server.py
COPY web /app/web
COPY download_fonts.py /tmp/download_fonts.py
RUN python3 /tmp/download_fonts.py --output-dir /app/web/fonts \
    && rm /tmp/download_fonts.py

EXPOSE 8090

ENV HTTP_PORT=8090

CMD ["python3", "/app/server.py"]
