import grpc
import time
import threading
import argparse
import random
import logging
import sys

import streaming_pb2
import streaming_pb2_grpc

# --- 로깅 설정 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)s - %(message)s',
    stream=sys.stdout
)

def generate_messages(client_id: str, channel_id: int, stream_id: int):
    """스트림 요청 메시지를 생성하는 제너레이터"""
    i = 0
    while True:
        request = streaming_pb2.TextRequest(
            message=f"Msg num {i}",
            client_id=client_id,
            channel_id=int(channel_id)
        )
        yield request
        i += 1
        time.sleep(random.uniform(0.1, 0.3)) # 약간의 무작위성을 추가

def run_single_stream(stub: streaming_pb2_grpc.StreamerStub, client_id: str, channel_id: int, stream_id: int):
    """
    하나의 gRPC 스트림을 실행하는 워커 함수.
    이 함수는 채널이 유효하다고 가정하고, 스트림이 끊어지면 그냥 종료됩니다.
    """
    log_prefix = f"[Client: {client_id}, Chan: {channel_id}, Stream: {stream_id}]"
    try:
        logging.info(f"{log_prefix} Stream starting.")
        
        # 제너레이터를 통해 요청 스트림 생성
        request_iterator = generate_messages(client_id, channel_id, stream_id)
        
        # 서버로 스트림 요청 시작
        response_iterator = stub.ProcessTextStream(request_iterator)
        
        # 서버로부터 오는 응답 처리 (이 예제에서는 거의 발생하지 않음)
        for response in response_iterator:
            logging.info(f"{log_prefix} Received response: {response.message_count}")

    except grpc.RpcError as e:
        # 연결 종료(UNAVAILABLE)나 클라이언트 취소(CANCELLED) 등은
        # 채널 관리자 레벨에서 처리해야 하므로 여기서는 단순히 로그만 남기고 종료합니다.
        if e.code() in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.CANCELLED]:
            logging.warning(f"{log_prefix} Stream terminated with code: {e.code()}. Channel needs reconnect.")
        else:
            logging.error(f"{log_prefix} Unexpected RPC error: {e.code()} - {e.details()}")
    except Exception as e:
        logging.error(f"{log_prefix} Unexpected Python error: {e}")
    finally:
        logging.info(f"{log_prefix} Stream thread finished.")

def manage_channel(server_address: str, credentials, client_id: str, channel_id: int, streams_on_this_channel: int):
    """
    하나의 채널과 그 위에서 동작하는 여러 스트림들을 관리합니다.
    채널 연결이 끊어지면, 채널을 재생성하고 그 위의 스트림들을 다시 시작합니다.
    """
    thread_name = f"ChannelManager-{channel_id}"
    threading.current_thread().name = thread_name

    while True:
        channel = None
        stream_threads = []
        try:
            logging.info(f"Creating new channel to {server_address}")
            channel = grpc.secure_channel(
                server_address,
                credentials,
                options=(('grpc.ssl_target_name_override', 'grpc.example.com'),)
            )
            stub = streaming_pb2_grpc.StreamerStub(channel)

            for i in range(streams_on_this_channel):
                stream_id = (channel_id * 100) + i # 고유한 스트림 ID 생성
                thread = threading.Thread(
                    target=run_single_stream,
                    args=(stub, client_id, channel_id, stream_id),
                    daemon=True
                )
                stream_threads.append(thread)
                thread.start()
            
            # 모든 스트림 스레드가 종료될 때까지 대기
            # 하나라도 종료되면 채널에 문제가 생긴 것이므로 루프를 다시 시작해 재연결
            for t in stream_threads:
                t.join()

        except Exception as e:
            logging.error(f"Error in channel manager: {e}")
        finally:
            if channel:
                channel.close()
            logging.warning("Channel connection lost. Re-establishing all streams on this channel.")
            time.sleep(random.uniform(1, 5)) # Jitter

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="gRPC multiplexing client")
    parser.add_argument("server_address", help="The gRPC server address (e.g., grpc.example.com:443)")
    parser.add_argument("--client-id", type=str, required=True, help="A unique identifier for this client instance")
    parser.add_argument("--streams", type=int, default=10, help="Total number of streams per client instance")
    parser.add_argument("--channels", type=int, default=1, help="Number of channels to distribute streams over")
    parser.add_argument("--cert_file", help="Path to the server's certificate file", required=True)
    args = parser.parse_args()

    # --- [조치] 파싱된 인수를 확인하기 위한 로그 추가 ---
    print(f"CLIENT STARTING ON '{args.client_id}'. ARGS: {args}", flush=True)
    
    try:
        with open(args.cert_file, 'rb') as f:
            root_certs = f.read()
    except FileNotFoundError:
        logging.critical(f"Certificate file not found at '{args.cert_file}'")
        sys.exit(1)

    credentials = grpc.ssl_channel_credentials(root_certificates=root_certs)

    manager_threads = []
    
    # 스트림을 채널에 분배
    base_streams_per_channel = args.streams // args.channels
    remainder_streams = args.streams % args.channels

    for i in range(args.channels):
        num_streams = base_streams_per_channel
        if i < remainder_streams:
            num_streams += 1
        
        if num_streams == 0:
            continue

        thread = threading.Thread(
            target=manage_channel,
            args=(args.server_address, credentials, args.client_id, i, num_streams)
        )
        manager_threads.append(thread)
        thread.start()

    try:
        for thread in manager_threads:
            thread.join()
    except KeyboardInterrupt:
        logging.info("Shutdown signal received. Exiting.")