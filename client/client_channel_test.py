import grpc
import time
import threading
import argparse
import random
import queue
import streaming_pb2
import streaming_pb2_grpc

class MessageQueueIterator:
    def __init__(self, q: queue.Queue):
        self._queue = q
    def __iter__(self):
        return self
    def __next__(self):
        item = self._queue.get(block=True)
        if item is None:
            raise StopIteration
        return item

def generate_and_queue_messages(q: queue.Queue, stream_id: int, channel_id: int, client_id: str):
    try:
        i = 0
        while True:
            # 컴파일이 성공했다면 이 부분은 오류 없이 실행됩니다.
            request = streaming_pb2.TextRequest(
                message=f"Msg: {i}",
                channel_id=channel_id,
                client_id=client_id
            )
            q.put(request)
            i += 1
            time.sleep(0.1)
    finally:
        q.put(None)

def run_stream_on_channel(channel: grpc.Channel, stream_id: int, channel_id: int, client_id: str):
    while True:
        log_prefix = f"[Client: {client_id}, Channel: {channel_id}, Stream: {stream_id}]"
        try:
            stub = streaming_pb2_grpc.StreamerStub(channel)
            print(f"{log_prefix} Starting...")

            request_queue = queue.Queue(maxsize=100)
            
            producer_thread = threading.Thread(
                target=generate_and_queue_messages,
                args=(request_queue, stream_id, channel_id, client_id),
                daemon=True
            )
            producer_thread.start()

            response_iterator = stub.ProcessTextStream(MessageQueueIterator(request_queue))

            for response in response_iterator:
                print(f"{log_prefix} Server processed {response.message_count} messages.")

        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.UNAVAILABLE:
                print(f"{log_prefix} Connection likely closed. Reconnecting...")
            else:
                print(f"{log_prefix} RPC error: {e.code()} - {e.details()}. Retrying...")
        except Exception as e:
            print(f"{log_prefix} Unexpected error: {type(e).__name__} - {e}. Retrying...")

        reconnect_delay = random.uniform(1, 5)
        print(f"{log_prefix} Reconnecting in {reconnect_delay:.2f} seconds.")
        time.sleep(reconnect_delay)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="gRPC stream load balancing test client")
    parser.add_argument("server_address", help="The gRPC server address")
    parser.add_argument("--client-id", type=str, default="default-client", help="A unique identifier for this client instance")
    parser.add_argument("--streams", type=int, default=10, help="Total streams PER CLIENT")
    parser.add_argument("--channels", type=int, default=1, help="Number of channels PER CLIENT")
    parser.add_argument("--cert_file", help="Path to the server's certificate file", required=True)
    args = parser.parse_args()

    # ... (이하 main 함수는 이전과 동일하며, 수정할 필요 없습니다) ...
    try:
        with open(args.cert_file, 'rb') as f:
            root_certs = f.read()
    except FileNotFoundError:
        print(f"Error: Certificate file not found at '{args.cert_file}'")
        exit(1)
    
    credentials = grpc.ssl_channel_credentials(root_certificates=root_certs)
    
    print(f"Creating a pool of {args.channels} channels...")
    channel_pool = []
    for i in range(args.channels):
        channel = grpc.secure_channel(
            args.server_address, 
            credentials, 
            options=(('grpc.ssl_target_name_override', 'grpc.example.com'),)
        )
        channel_pool.append(channel)
        print(f"Channel-{i} created for {args.server_address}")

    threads = []
    try:
        for i in range(args.streams):
            channel_index = i % args.channels
            selected_channel = channel_pool[channel_index]
            
            thread = threading.Thread(
                target=run_stream_on_channel, 
                args=(selected_channel, i, channel_index, args.client_id)
            )
            threads.append(thread)
            thread.start()
            
            time.sleep(random.uniform(0.1, 0.5))

        for thread in threads:
            thread.join()
            
    except KeyboardInterrupt:
        print("\nUser interrupted. Shutting down.")
    finally:
        print("Closing all channels...")
        for channel in channel_pool:
            channel.close()