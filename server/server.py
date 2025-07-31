import asyncio
import grpc
import logging
import sys
import time

# Protobuf 및 헬스 체크 관련 import
import streaming_pb2
import streaming_pb2_grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

# OpenTelemetry 및 Prometheus 관련 import
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from prometheus_client import start_http_server, Gauge

# 공식 gRPC 계측 라이브러리 import
from opentelemetry.instrumentation.grpc import GrpcAioInstrumentorServer

# --- 로깅 설정 ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    stream=sys.stdout)

# --- OpenTelemetry 설정 ---
resource = Resource(attributes={"service.name": "vac-hub-service"})
prometheus_reader = PrometheusMetricReader()
provider = MeterProvider(metric_readers=[prometheus_reader], resource=resource)

# gRPC 비동기 서버 계측
GrpcAioInstrumentorServer().instrument(meter_provider=provider)

# 비동기 호환 Health Servicer
class AsyncHealthServicer(health_pb2_grpc.HealthServicer):
    def __init__(self):
        self._server_status = {"": health_pb2.HealthCheckResponse.SERVING}
        self._lock = asyncio.Lock()

    async def Check(self, request, context):
        async with self._lock:
            status = self._server_status.get(request.service)
            if status is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                return health_pb2.HealthCheckResponse()
            return health_pb2.HealthCheckResponse(status=status)

    def set(self, service, status):
        self._server_status[service] = status

# 커스텀 메트릭 정의
custom_meter = provider.get_meter("grpc.server.python.streaming_service.custom")
processed_message_counter = custom_meter.create_counter(
    name="app_messages_processed_count",
    unit="1",
    description="The total number of messages processed by the streaming service"
)
active_streams_gauge = Gauge(
    'grpc_server_active_streams',
    'The number of currently active gRPC streams.'
)

# 스트리밍 요청 처리 클래스
class StreamerService(streaming_pb2_grpc.StreamerServicer):
    async def ProcessTextStream(self, request_iterator, context):
        active_streams_gauge.inc()
        log_prefix = "[Client: Unknown, Channel: Unknown]"
        message_count = 0
        
        try:
            # <<< [핵심 수정] Python 3.9와 호환되도록 __anext__() 와 예외 처리를 사용합니다. >>>
            try:
                first_request = await request_iterator.__anext__()
            except StopAsyncIteration:
                first_request = None
            
            if not first_request:
                logging.info("Stream opened but received no messages.")
                return streaming_pb2.TextResponse(message_count=0)

            client_id = first_request.client_id
            channel_id = first_request.channel_id
            log_prefix = f"[Client: {client_id}, Channel: {channel_id}]"
            logging.info(f"{log_prefix} Stream opened. Active streams: {active_streams_gauge._value.get()}")
            message_count = 1
            processed_message_counter.add(1)

            async for request in request_iterator:
                message_count += 1
                processed_message_counter.add(1)
                await asyncio.sleep(0.01)

            logging.info(f"{log_prefix} Stream closed normally by client. Processed {message_count} messages.")
            return streaming_pb2.TextResponse(message_count=message_count)
        
        except grpc.aio.AioRpcError as e:
            if e.code() == grpc.StatusCode.CANCELLED:
                logging.info(f"{log_prefix} Stream cancelled by client after {message_count} messages.")
            else:
                logging.warning(f"{log_prefix} An RPC error occurred: code={e.code()}, details={e.details()}")
            return streaming_pb2.TextResponse(message_count=message_count)
            
        finally:
            active_streams_gauge.dec()
            logging.info(f"{log_prefix} Stream finished. Active streams: {active_streams_gauge._value.get()}")

# 비동기 서버 실행 함수
async def serve():
    server_options = [
        # 10분마다 연결을 종료하도록 설정
        ('grpc.max_connection_age_ms', 600000), 
        # 10분 연결 종료 후 60초의 유예 시간을 줌
        ('grpc.max_connection_age_grace_ms', 60000),
        # --- [핵심 추가] 연결 종료 시간에 무작위성을 부여합니다 ---
        # 0.1은 10%를 의미. 즉, 10분의 10%인 1분(60초) 내에서
        # 연결 종료 시점을 무작위로 분산시킵니다.
        ('grpc.max_connection_age_jitter', 0.1) 
    ]
    
    server = grpc.aio.server(options=server_options)
    
    streaming_pb2_grpc.add_StreamerServicer_to_server(StreamerService(), server)
    
    health_servicer = AsyncHealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    
    server.add_insecure_port("[::]:50051")
    await server.start()
    logging.info(f"gRPC server started on port 50051 with max_connection_age={server_options[0][1]}ms, grace={server_options[1][1]}ms.")
    await server.wait_for_termination()

# 메인 실행부
if __name__ == "__main__":
    try:
        start_http_server(port=9464, addr='0.0.0.0')
        logging.info("Prometheus metrics server started on port 9464.")
        asyncio.run(serve())
    except KeyboardInterrupt:
        logging.info("Shutting down due to KeyboardInterrupt...")
    finally:
        logging.info("Shutting down...")
        provider.shutdown()