# gke_autopilot_grpc_with_otel
OTEL & Prometheus testing in GKE autopilot cluster with Cloud Load Balancer

이 가이드는 Python을 기준으로 작성되었으며, Google Cloud Load Balancer (GCLB)를 사용하여 GKE에 배포된 gRPC 서버에 부하를 발생시키는 것을 목표로 합니다.

### **전체 테스트 시나리오 요약**

1.  **GKE Autopilot 클러스터 생성:** Managed Service for Prometheus가 활성화된 클러스터를 준비합니다.
2.  **테스트용 gRPC 서버 개발:** OpenTelemetry로 계측된 간단한 스트리밍 gRPC 서버를 만듭니다.
3.  **테스트용 gRPC 클라이언트 개발:** 서버로 스트리밍 요청을 보내 부하를 발생시키는 클라이언트를 만듭니다.
4.  **컨테이너화 및 배포:** gRPC 서버를 컨테이너 이미지로 빌드하고, GKE에 Deployment, Service, **BackendConfig**, **Gateway**, **HTTPRoute**, **HPA**를 배포합니다.
5.  **테스트 실행 및 검증:** 클라이언트를 실행하여 부하를 발생시키고, Cloud Monitoring과 HPA 동작을 통해 메트릭 수집 및 오토스케일링을 확인합니다.

---

### **Phase 1: GKE 클러스터 및 환경 준비**

1.  **gcloud 프로젝트 설정:**
    ```bash
    # (이미 설정하셨다면 생략)
    gcloud config set project [YOUR_PROJECT_ID]
    gcloud config set compute/region asia-northeast3

    # 설정 확인
    gcloud config list
    ```

2.  **환경 변수 설정:**
    ```bash
    export PROJECT_ID=$(gcloud config list --format 'value(core.project)')
    export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
    export REGION=$(gcloud config list --format 'value(compute.region)')
    export CLUSTER_NAME=grpc-otel-test
    ```

3.  **필수 API 활성화:**
    ```bash
    # 필요한 API 활성화 (mesh.googleapis.com 제외)
    gcloud services enable \
        container.googleapis.com \
        monitoring.googleapis.com \
        artifactregistry.googleapis.com \
        cloudbuild.googleapis.com \
        --project=$PROJECT_ID
    ```

4.  **GKE Autopilot 클러스터 생성:**
    ```bash
    gcloud container clusters create-auto $CLUSTER_NAME \
        --release-channel=regular \
        --location=$REGION \
        --project=$PROJECT_ID
    ```
    *생성까지 몇 분 정도 소요됩니다.*

5.  **생성된 클러스터에 Gateway API 기능 추가:**
    *   GKE Gateway Controller를 사용하여 Cloud Load Balancer를 프로비저닝하는 데 필요합니다.
    ```bash
    gcloud container clusters update $CLUSTER_NAME \
        --location=$REGION \
        --gateway-api=standard
    ```

6.  **클러스터 인증 정보 가져오기:**
    ```bash
    gcloud container clusters get-credentials $CLUSTER_NAME --location $REGION --project $PROJECT_ID
    ```

7.  **Managed Prometheus 활성화 여부 확인:**
    *   Autopilot 클러스터는 기본적으로 활성화되어 있습니다.
    ```bash
    gcloud container clusters describe $CLUSTER_NAME \
        --region=$REGION \
        --format="get(monitoringConfig.managedPrometheusConfig.enabled)"
    # "true"가 출력되어야 합니다.
    ```

8.  **테스트용 TLS 인증서 생성**

    먼저 로컬 머신에서 테스트에 사용할 자체 서명 인증서를 만듭니다. grpc.example.com이라는 임시 도메인 이름으로 인증서를 발급하겠습니다.

    ```bash
    # grpc-hpa-test/k8s 디렉토리에서 실행하세요.
    cd ~/grpc-hpa-test/k8s
    
    # 자체 서명 인증서와 키 생성
    openssl req -x509 -newkey rsa:2048 -nodes -keyout tls.key -out tls.crt -subj "/CN=grpc.example.com"
    ``` 

---

### **Phase 2: 테스트용 gRPC 서버 애플리케이션**

이 서버는 클라이언트로부터 텍스트 스트림을 받고, 수신한 메시지 수를 계산하여 반환합니다. OpenTelemetry를 통해 Prometheus 메트릭을 노출합니다.

1.  **Python용 가상환경 설정:**
    ```bash
    # 프로젝트 최상위 디렉토리로 이동
    cd ~/grpc-hpa-test

    # 가상환경 생성
    python3 -m venv venv

    # 가상환경 활성화
    source venv/bin/activate
    ```

2.  **필요 Library 설치:**
    ```bash
    # grpc-hpa-test/server 디렉토리 안에 있는지 확인합니다.
    cd ~/grpc-hpa-test/server

    # requirements.txt 파일을 사용하여 라이브러리 설치
    pip install -r requirements.txt
    ```

3.  **Protobuf 컴파일:**
    ```bash
    python -m grpc_tools.protoc -I./protos --python_out=. --grpc_python_out=. ./protos/streaming.proto
    ```

4.  **Python 가상환경 비활성화:**
    ```bash
    deactivate
    ```

---

### **Phase 3: 컨테이너화**

1.  **Artifact Registry 저장소 생성 (Option):**
    ```bash
    gcloud artifacts repositories create grpc-test-repo \
    --repository-format=docker \
    --location=$REGION \
    --description="Repository for gRPC HPA test images"
    ```

2.  **Cloud Build를 사용하여 이미지 빌드 및 Artifact Registry에 푸시:**
    ```bash
    cd ~/grpc-hpa-test
    
    # 1. 타임스탬프 기반의 고유한 태그를 생성하고 환경 변수에 저장합니다.
    export IMAGE_TAG=$(date -u +%Y%m%d-%H%M%S)
    echo "A new unique tag has been created: $IMAGE_TAG"
    
    # (grpc-hpa-test 디렉토리에서 실행)
    # 2. 이 고유한 태그를 사용하여 이미지를 빌드하고 푸시합니다.
    gcloud builds submit ./server --tag="${REGION}-docker.pkg.dev/${PROJECT_ID}/grpc-test-repo/vac-hub-test:${IMAGE_TAG}"
    ```

---

### **Phase 5: GKE 배포 (Cloud Load Balancer 사용)**

GKE Gateway Controller가 관리하는 표준 Cloud Load Balancer를 사용하합니다.

2.  **GKE 배포 매니페스트 (`application-gateway.yaml`):**
    *   `~/grpc-hpa-test/k8s/namespace.yaml` 파일을 아래 내용으로 작성합니다.
    ```yaml
    # 1. 애플리케이션을 위한 Namespace
    apiVersion: v1
    kind: Namespace
    metadata:
      name: grpc-test
    ```
    
    *   `~/grpc-hpa-test/k8s/application-gateway.yaml` 파일을 아래 내용으로 작성합니다.
    ```yaml
    # 2. HealthCheckPolicy: Gateway API를 위한 상태 확인 설정 리소스
    # "상태 확인은 GRPC로 하라"고 명시
    # https://cloud.google.com/kubernetes-engine/docs/how-to/configure-gateway-resources#configure_health_check
    apiVersion: networking.gke.io/v1
    kind: HealthCheckPolicy
    metadata:
      name: vac-hub-grpc-health-check-policy
      namespace: grpc-test
    spec:
      # 이 정책이 적용될 대상을 명시적으로 지정합니다.
      targetRef:
        group: ""
        kind: Service
        name: vac-hub-test-svc
      default:
        checkIntervalSec: 15
        healthyThreshold: 1
        unhealthyThreshold: 2
        config:
          type: GRPC
          grpcHealthCheck:
            port: 50051
    ---
    # 3. GCPBackendPolicy: "클라이언트 연결 후 10분간 데이터가 없어도 끊지 마"
    apiVersion: networking.gke.io/v1
    kind: GCPBackendPolicy
    metadata:
      name: vac-hub-timeout-policy
      namespace: grpc-test
    spec:
      # 정책이 적용될 Service를 명시적으로 지정합니다.
      targetRef:
        group: ""
        kind: Service
        name: vac-hub-test-svc
      default:
        # 유휴 연결 타임아웃 (길게 설정)
        timeoutSec: 600
    ---        
    # 4. 애플리케이션 Service (ClusterIP)
    # HealthCheckPolicy를 어노테이션으로 연결
    apiVersion: v1
    kind: Service
    metadata:
      name: vac-hub-test-svc
      namespace: grpc-test
      annotations:
        # Gateway API가 Pod를 직접 타겟팅(NEG)하도록 설정
        cloud.google.com/neg: '{"gateway": true}'
    spec:
      type: ClusterIP
      selector:
        app: vac-hub-test
      ports:
      - name: grpc
        protocol: TCP
        port: 50051
        targetPort: 50051
        # ADDED: 이 포트가 gRPC 프로토콜을 사용함을 명시적으로 알려줍니다.
        # appProtocol: GRPC
        appProtocol: kubernetes.io/h2c
    ---
    # 5. Kubernetes Gateway: GKE에 Cloud Load Balancer 생성을 요청합니다.
    apiVersion: gateway.networking.k8s.io/v1
    kind: Gateway
    metadata:
      name: vac-hub-gateway
      namespace: grpc-test
    spec:
      # 표준 GKE L7 로드밸런서 클래스를 사용합니다.
      #gatewayClassName: gke-l7-gxlb
      gatewayClassName: gke-l7-global-external-managed  
      listeners:
      - name: https
        protocol: HTTPS
        port: 443
        allowedRoutes:
          namespaces:
            from: Same
        tls:
          mode: Terminate # 로드밸런서에서 TLS 종료
          certificateRefs:
          - name: grpc-cert # 로컬에서 생성한 TLS Secret
            kind: Secret # 참조하는 리소스의 종류
            group: ""
    ---
    # 6. HTTPRoute: Gateway로 들어온 트래픽을 서비스로 라우팅합니다.
    # gRPC는 HTTP/2 기반이므로 HTTPRoute로 처리가능합니다.
    # GCPBackendPolicy(타임아웃용)를 필터로 연결
    apiVersion: gateway.networking.k8s.io/v1
    kind: HTTPRoute
    metadata:
      name: vac-hub-http-route
      namespace: grpc-test
    spec:  
      parentRefs:
      - kind: Gateway
        name: vac-hub-gateway
      hostnames:
      - "grpc.example.com"
    #    sectionName: https
      rules:
      - backendRefs:
        - name: vac-hub-test-svc
          port: 50051
    ---
    # --- 수정: gRPC 앱 배포 ---
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: vac-hub-test
      namespace: grpc-test
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: vac-hub-test
      template:
        metadata:
          labels:
            app: vac-hub-test
        spec:
          terminationGracePeriodSeconds: 60
          containers:
          - name: vac-hub-test-server
            image: "${REGION}-docker.pkg.dev/${PROJECT_ID}/grpc-test-repo/vac-hub-test:${IMAGE_TAG}"
            # 환경 변수를 통해 Collector 서비스 주소를 전달합니다.
            env:
            - name: OTEL_COLLECTOR_ENDPOINT
              value: "otel-collector.grpc-test.svc.cluster.local:4317"
            ports:
            - containerPort: 50051
              name: grpc
            # --- Prometheus 포트 8000은 더 이상 필요 없습니다 ---
            readinessProbe:
              grpc:
                port: 50051
              initialDelaySeconds: 5
    ---
    # HPA (변경 없음. 최종 수정된 External 타입 유지)
    apiVersion: autoscaling/v2
    kind: HorizontalPodAutoscaler
    metadata:
      name: vac-hub-test-hpa
      namespace: grpc-test
    spec:
      scaleTargetRef:
        apiVersion: apps/v1
        kind: Deployment
        name: vac-hub-test
      minReplicas: 1
      maxReplicas: 5
      metrics:
      - type: External
        external:
          metric:
            name: prometheus.googleapis.com|grpc_server_active_calls_gauge|gauge
            selector:
              matchLabels:
                metric.labels.app: vac-hub-test
          target:
            type: AverageValue
            averageValue: "3"
    ---
    # --- 수정: PodMonitoring ---
    # https://cloud.google.com/stackdriver/docs/managed-prometheus/setup-managed#gmp-pod-monitoring
    apiVersion: monitoring.googleapis.com/v1
    kind: PodMonitoring
    metadata:
      name: otel-collector-pod-monitoring # 이름 변경 (더 명확하게)
      namespace: grpc-test
    spec:
      # ### 가장 중요한 수정 ###
      # 이제 gRPC 앱이 아닌, otel-collector 파드를 감시합니다.
      selector:
        matchLabels:
          app: otel-collector
      endpoints:
      # Collector는 8888 포트에서 Prometheus 형식의 메트릭을 노출합니다.
      - port: prometheus # Collector Deployment의 포트 이름과 일치
        path: /metrics
        interval: 30s
    ```

3.  **GKE에 배포:**
    ```bash
    # 이전에 적용된 리소스가 꼬이는 것을 방지하기 위해 delete 후 apply를 권장합니다.
    cd ~/grpc-hpa-test/k8s
    kubectl delete -f ./namespace.yaml --ignore-not-found
    kubectl apply -f ./namespace.yaml
    # Kubernetes TLS Secret 만들기
    kubectl create secret tls grpc-cert -n grpc-test --key tls.key --cert tls.crt --dry-run=client -o yaml | kubectl apply -f -
    kubectl apply -f ./gateway.yaml
    envsubst < deployment.yaml | kubectl apply -f -
    ```

---

### **Phase 6: 테스트 실행 및 결과 검증**

1.  **배포 상태 확인:**
    ```bash
    # Secret 확인
    kubectl get secret grpc-cert -n grpc-test

    # Deployment와 Service가 정상적으로 생성되었는지 확인
    kubectl get deployment,svc -n grpc-test

    # Pod 상태 확인
    kubectl get pods -n grpc-test
    ```

2.  **Gateway 외부 IP 확인:**
    *   GKE가 Cloud Load Balancer를 프로비저닝하고 외부 IP를 할당하는 데 몇 분 정도 소요됩니다.
    ```bash
    # -w 플래그로 IP가 할당될 때까지 실시간으로 확인
    kubectl get gateway vac-hub-gateway -n grpc-test -w

    # NAME              CLASS          ADDRESS         READY   AGE
    # vac-hub-gateway   gke-l7-gxlb    34.12.34.56     True    2m
    ```
    `ADDRESS` 필드에 나타나는 IP 주소를 복사합니다.

3.  **Gateway backend protocol 확인:**
    *   Cloud Load Balancer backend protocol 확인
    ```bash
    gcloud compute backend-services list \
        --filter="name~grpc-test AND name~vac-hub-test-svc" \
        --format="value(name)" \
    | xargs -I {} gcloud compute backend-services describe {} --global --format="value(protocol)"
    ```

4.  **클라이언트 실행:**
    *   로컬 터미널에서 가상환경을 활성화하고 클라이언트를 실행하여 부하를 발생시킵니다.
    ```bash
    # 1. 서버의 인증서 파일을 클라이언트 디렉토리로 복사합니다.
    cp ~/grpc-hpa-test/k8s/tls.crt ~/grpc-hpa-test/client/
    
    # 2. 가상환경 활성화 및 client 디렉토리로 이동
    cd ~/grpc-hpa-test
    source venv/bin/activate
    cd ~/grpc-hpa-test/client
    
    # 3. 클라이언트 실행 (GATEWAY_IP:443 포트와 --cert_file 옵션 사용)
    python client.py [GATEWAY_EXTERNAL_IP]:443 --streams 10 --cert_file ./tls.crt
    ```

5.  **HPA 동작 확인:**
    *   새로운 터미널을 열고 HPA가 메트릭을 수집하고 파드 개수를 조정하는지 확인합니다.
    ```bash
    # 1분 간격으로 HPA 상태를 확인
    kubectl get hpa vac-hub-test-hpa -n grpc-test -w
    ```
    *   출력의 `TARGETS` 컬럼에 `.../3` 과 같이 현재 메트릭 값과 목표 값이 표시됩니다. 부하가 증가하면 `REPLICAS` 수가 1에서 점차 늘어나는 것을 볼 수 있습니다.

6.  **Cloud Monitoring에서 메트릭 확인:**
    *   Google Cloud Console에서 **Monitoring > Metrics Explorer**로 이동합니다.
    *   **리소스 유형(Resource type)** 에서 `GKE Prometheus Target`을 선택합니다.
    *   **측정항목(Metric)** 에서 `grpc_server_active_calls_gauge` 를 검색하여 선택합니다.
    *   `Group By` 옵션에 `pod` 를 추가하면 각 파드별 활성 gRPC 연결 수를 그래프로 확인할 수 있습니다. HPA에 의해 파드가 늘어나는 모습을 시각적으로 볼 수 있습니다.
    * CSM 관련 대시보드는 보이지 않지만, **부하 분산(Load Balancing)** 메뉴에서 생성된 로드밸런서를 클릭하여 트래픽, 백엔드 상태 등의 상세 정보를 확인할 수 있습니다.

7.  **테스트 종료 후 정리:**
    ```bash
    # Python 가상환경 비활성화
    deactivate

    # GKE 리소스 삭제
    kubectl delete -f ~/grpc-hpa-test/k8s/application.yaml

    # GKE 클러스터 삭제 (선택 사항)
    # gcloud container clusters delete $CLUSTER_NAME --location=$REGION
    ```
