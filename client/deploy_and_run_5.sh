#!/bin/bash

# --- 스크립트를 시작하기 전에 이 변수들을 설정하세요 ---

# 1. Google Cloud 프로젝트 ID
PROJECT_ID="p-khm8-dev-svc"

# 2. GCE VM을 생성할 위치와 이름
GCE_ZONE="asia-northeast3-c"
GCE_INSTANCE_NAME="grpc-client-vm-5" # 이 부분은 grpc-client-vm-1, 2, 3 등으로 바꿔서 실행

# 3. 테스트할 gRPC 서버의 외부 IP 주소
SERVER_IP="34.102.148.180"

# 4. 로컬에 준비해둔 클라이언트 파일들이 있는 디렉토리
LOCAL_DIR="."

# 5. GCE VM에 파일들을 업로드할 디렉토리
REMOTE_DIR="~/client"

# --- 스크립트 메인 로직 ---

set -e

echo ">>> 1. GCP 프로젝트 설정..."
gcloud config set project $PROJECT_ID

echo ">>> 2. GCE VM 인스턴스 생성 또는 확인..."
if ! gcloud compute instances describe $GCE_INSTANCE_NAME --zone $GCE_ZONE >/dev/null 2>&1; then
  echo "    '$GCE_INSTANCE_NAME' VM을 찾을 수 없습니다. 새로 생성합니다..."
  gcloud compute instances create $GCE_INSTANCE_NAME \
    --zone $GCE_ZONE \
    --machine-type=e2-medium \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --shielded-secure-boot
else
  echo "    '$GCE_INSTANCE_NAME' VM이 이미 존재합니다. 생성을 건너뜁니다."
fi

# --- [핵심 수정] ---
# 단계 A: 원격지 디렉토리를 로컬에서 먼저 깨끗하게 정리합니다.
echo ">>> 3A. GCE VM의 원격 디렉토리를 강제로 초기화합니다..."
gcloud compute ssh $GCE_INSTANCE_NAME --zone $GCE_ZONE --command "pkill -9 -f client_multiplex.py || true; rm -rf ${REMOTE_DIR}; mkdir -p ${REMOTE_DIR}" --troubleshoot

# 단계 B: 애플리케이션 파일들만 원격지의 깨끗한 디렉토리로 복사합니다.
echo ">>> 3B. 클라이언트 애플리케이션 파일들을 GCE VM으로 복사합니다..."
gcloud compute scp --recurse ${LOCAL_DIR}/*.py ${LOCAL_DIR}/*.txt ${LOCAL_DIR}/*.crt ${LOCAL_DIR}/protos ${GCE_INSTANCE_NAME}:${REMOTE_DIR} --zone ${GCE_ZONE}

# 단계 C: 원격 실행 스크립트를 생성합니다 (가상 환경 사용).
echo ">>> 3C. 원격 실행 스크립트를 생성합니다 (가상 환경 사용)..."
cat << EOF > remote_run_only.sh
#!/bin/bash
set -e

echo '    [VM] 클라이언트 디렉토리로 이동...'
cd ${REMOTE_DIR}

# Debian 11용이었던 불필요한 라인 제거 (Debian 12에는 영향 없음)
# sudo rm -f /etc/apt/sources.list.d/backports.list

echo '    [VM] apt 패키지 목록 업데이트 및 필수 패키지 설치...'
sudo DEBIAN_FRONTEND=noninteractive apt-get update -yq > /dev/null
# python3-pip와 함께 가상 환경을 위한 python3-venv 설치
sudo DEBIAN_FRONTEND=noninteractive apt-get install -yq python3-pip python3-venv > /dev/null

# --- [핵심 수정 사항] ---
echo '    [VM] 파이썬 가상 환경 생성 및 활성화...'
# venv 라는 이름의 가상 환경 디렉토리 생성
python3 -m venv venv
# 생성된 가상 환경 활성화
source venv/bin/activate
# --- [수정 끝] ---

echo '    [VM] requirements.txt로 파이썬 패키지 설치 (가상 환경 내부)...'
if [ -f "requirements.txt" ]; then
    # 이제 pip는 시스템이 아닌 가상 환경에 패키지를 설치하므로 안전합니다.
    pip install -r requirements.txt
else
    echo '    [VM] requirements.txt 파일이 없어 패키지 설치를 건너뜁니다.'
fi

echo '    [VM] 백그라운드에서 gRPC 클라이언트 실행...'
# 활성화된 가상 환경의 python을 사용하여 스크립트를 실행합니다.
nohup python3 -u client_multiplex.py ${SERVER_IP}:443 \
    --client-id ${GCE_INSTANCE_NAME} \
    --channels 3 \
    --streams 30 \
    --cert_file ./tls.crt > client.log 2>&1 &
    
echo "    [VM] 클라이언트 실행 완료! 로그는 \${REMOTE_DIR}/client.log 에서 확인하세요."
EOF

# 단계 D: 실행 전용 스크립트를 원격지로 복사합니다.
echo ">>> 3D. 실행 전용 스크립트를 GCE VM으로 복사합니다..."
gcloud compute scp remote_run_only.sh ${GCE_INSTANCE_NAME}:${REMOTE_DIR} --zone ${GCE_ZONE}

# 단계 E: 원격지에서 실행 전용 스크립트를 실행합니다.
echo ">>> 4. GCE VM에 접속하여 원격 스크립트 실행..."
gcloud compute ssh $GCE_INSTANCE_NAME --zone $GCE_ZONE --command "bash ${REMOTE_DIR}/remote_run_only.sh"

echo ">>> 모든 작업이 성공적으로 완료되었습니다."
# 로컬에 생성된 임시 스크립트 파일 삭제
rm remote_run_only.sh