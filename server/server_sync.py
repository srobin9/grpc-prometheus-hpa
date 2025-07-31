import time
import grpc
from concurrent import futures
import logging
import os
import sys

# --- OpenTelemetry의 핵심 SDK 및 Resource 객체 ---
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
# --- Prometheus Exporter 관련 클래스 ---
from opentelemetry.exporter.prometheus import PrometheusMetricReader
# Prometheus 웹 서버를 시작하기 위한 함수 import
from prometheus_client import start_http_server, Gauge

# --- gRPC Observability 플러그인 ---
import grpc_observability

# Protobuf 및 헬스 체크 관련 import
import streaming_pb2
import streaming_pb2_grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

# --- 로깅 설정 ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    stream=sys.stdout)

# --- OpenTelemetry 설정 ---
resource = Resource(attributes={"service.name": "vac-hub-service"})
prometheus_reader = PrometheusMetricReader()
provider = MeterProvider(metric_readers=[prometheus_reader], resource=resource)
otel_plugin = grpc_observability.OpenTelemetryPlugin(meter_provider=provider)
otel_plugin.register_global()
custom_meter = provider.get_meter("grpc.server.python.streaming_service.custom")
processed_message_counter = custom_meter.create_counter(
    name="app_messages_processed_count",
    unit="1",
    description="The total number of messages processed by the streaming service"
)

# 이 메트릭은 /metrics 엔드포인트에 'grpc_server_active_streams' 라는 이름으로 노출됩니다.
active_streams_gauge = Gauge(
    'grpc_server_active_streams',
    'The number of currently active gRPC streams.'
)

# 스트리밍 요청 처리 클래스
class StreamerService(streaming_pb2_grpc.StreamerServicer):
    def ProcessTextStream(self, request_iterator, context):
        active_streams_gauge.inc()

        first_request = next(request_iterator, None)
        if not first_request:
            active_streams_gauge.dec()
            logging.info("Stream opened but received no messages.")
            return streaming_pb2.TextResponse(message_count=0)

        # <<< 핵심 수정 1: client_id와 channel_id를 모두 추출 >>>
        client_id = first_request.client_id
        channel_id = first_request.channel_id
        
        # <<< 핵심 수정 2: 두 정보를 모두 사용하여 로그 포맷 변경 >>>
        log_prefix = f"[Client: {client_id}, Channel: {channel_id}]"
        
        logging.info(f"{log_prefix} Stream opened. Active streams: {active_streams_gauge._value.get()}")

        message_count = 1
        try:
            for request in request_iterator:
                message_count += 1
                processed_message_counter.add(1)
                time.sleep(0.01)

            logging.info(f"{log_prefix} Stream closed normally. Processed {message_count} messages.")
            return streaming_pb2.TextResponse(message_count=message_count)
        
        except grpc.RpcError as e:
            # e가 code()와 details()를 가진 Call 객체인지 먼저 확인합니다.
            if isinstance(e, grpc.Call):
                if e.code() == grpc.StatusCode.CANCELLED:
                    logging.info(f"{log_prefix} Stream cancelled by client after {message_count} messages.")
                else:
                    logging.warning(f"{log_prefix} An RPC error occurred: code={e.code()}, details={e.details()}")
            else:
                # Call 객체가 아닌 다른 RpcError의 경우 (AttributeError 방지)
                logging.error(f"{log_prefix} A non-specific RPC error occurred: {e}")
            return streaming_pb2.TextResponse(message_count=message_count)
            
        finally:
            active_streams_gauge.dec()
            logging.info(f"{log_prefix} Stream finished. Active streams: {active_streams_gauge._value.get()}")
            
def serve():
    server_options = [
        # 10분으로 설정
        ('grpc.max_connection_age_ms', 600000), 
        # GOAWAY 신호를 보낸 후, 클라이언트가 진행 중인 요청을 마무리할 수 있도록 
        # 60초의 유예 시간을 줍니다. 이 시간 동안은 연결이 바로 끊기지 않습니다.
        ('grpc.max_connection_age_grace_ms', 60000)
    ]
    
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=server_options
    )
    
    streaming_pb2_grpc.add_StreamerServicer_to_server(StreamerService(), server)

    # 헬스 체크 서비스 추가
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    
    # 기본적으로 전체 서비스("")를 SERVING 상태로 설정
    # Kubernetes의 gRPC 프로브가 이 상태를 확인합니다.
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    
    server.add_insecure_port("[::]:50051")
    server.start()

    logging.info(f"gRPC server started on port 50051 with max_connection_age={server_options[0][1]}ms, grace={server_options[1][1]}ms.")
    server.wait_for_termination()

if __name__ == "__main__":
    try:
        # /metrics 엔드포인트를 제공할 HTTP 서버를 시작합니다
        start_http_server(port=9464, addr='0.0.0.0')
        logging.info("Prometheus metrics server started on port 9464.")
        
        serve()
    finally:
        logging.info("Shutting down...")
        provider.shutdown()