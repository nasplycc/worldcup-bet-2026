FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 数据目录通过 volume 挂载，容器内保留
VOLUME /app/data

EXPOSE 8088

CMD ["python", "server.py"]
