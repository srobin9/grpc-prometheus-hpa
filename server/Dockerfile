# 베이스 이미지
FROM python:3.9-slim

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 매니저를 업데이트하고, 컴파일에 필요한 build-essential 설치
# (가끔 네이티브 코드를 컴파일해야 하는 라이브러리를 위해 필요)
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

# 의존성 파일만 먼저 복사하여 Docker 캐시 활용 극대화
COPY requirements.txt .

# 의존성 설치
RUN pip install --no-cache-dir -r requirements.txt

# 나머지 소스 코드 복사
COPY . .

# Protobuf 컴파일
RUN python3 -m grpc_tools.protoc -I./protos --python_out=. --grpc_python_out=. ./protos/streaming.proto

# 포트 노출
EXPOSE 50051 8000

# 애플리케이션 실행 (python3로 명시)
# CMD ["python3", "server.py"]

# 컨테이너 시작 시, 설치된 패키지 목록을 출력한 후 서버 실행 (최종 디버깅)
CMD sh -c "echo '--- Installed packages inside container: ---' && pip list && echo '--- Starting server ---' && python3 server.py"
