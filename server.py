import asyncio
import websockets
import json # Sử dụng JSON để có thể dễ dàng mở rộng cấu trúc tin nhắn sau này
import datetime
import logging

logging.basicConfig(level=logging.INFO)

HOST = '127.0.0.1'
PORT = 8765

# {websocket: name}
clients = {}

async def broadcast(message, sender_websocket=None):
    """Gửi tin nhắn tới tất cả client đã đăng ký, trừ người gửi."""
    if not clients:
        logging.info("No clients connected, nothing to broadcast.")
        return

    # Chuẩn bị tin nhắn (có thể là JSON string)
    now = datetime.datetime.now().strftime('%H:%M:%S')
    # Gói tin nhắn vào một cấu trúc dict, sau đó chuyển thành JSON
    message_data = {
        "timestamp": now,
        "content": message
    }
    message_json = json.dumps(message_data)
    logging.info(f"Broadcasting: {message_json}")

    # Tạo danh sách client cần gửi đến (tránh thay đổi dict khi đang duyệt)
    # Gửi đồng thời tới các client bằng asyncio.gather
    tasks = []
    client_websockets = list(clients.keys()) # Tạo bản sao keys
    for client in client_websockets:
        if client != sender_websocket:
            tasks.append(client.send(message_json))

    if tasks:
        # Chờ tất cả các tác vụ gửi hoàn thành
        # return_exceptions=True để không dừng nếu một client bị lỗi
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Xử lý các lỗi (ví dụ: client đã ngắt kết nối khi đang gửi)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failed_client = client_websockets[i] # Client tương ứng với task bị lỗi
                logging.error(f"Failed to send message to {clients.get(failed_client, 'unknown')}: {result}")
                # Có thể cân nhắc xóa client lỗi ở đây, nhưng hàm unregister sẽ xử lý việc này
                # khi kết nối thực sự đóng từ phía client đó.

async def register(websocket):
    """Đăng ký client mới, yêu cầu và xác thực tên."""
    name = None
    try:
        # Yêu cầu tên từ client
        await websocket.send(json.dumps({"type": "system", "content": "Vui lòng nhập tên của bạn:"}))
        name_message = await websocket.recv()
        data = json.loads(name_message)

        if data.get("type") != "name_set" or not data.get("name"):
            await websocket.send(json.dumps({"type": "error", "content": "Thông tin tên không hợp lệ."}))
            await websocket.close(reason="Invalid name info")
            logging.warning(f"Connection closed: Invalid name info from {websocket.remote_address}")
            return None # Trả về None nếu đăng ký thất bại

        name = data["name"].strip()
        if not name:
            await websocket.send(json.dumps({"type": "error", "content": "Tên không được để trống."}))
            await websocket.close(reason="Empty name")
            logging.warning(f"Connection closed: Empty name from {websocket.remote_address}")
            return None

        # Kiểm tra tên trùng lặp
        if name in clients.values():
            await websocket.send(json.dumps({"type": "error", "content": f"Tên '{name}' đã được sử dụng."}))
            await websocket.close(reason="Name already taken")
            logging.warning(f"Connection closed: Name '{name}' taken - {websocket.remote_address}")
            return None

        # Đăng ký thành công
        clients[websocket] = name
        logging.info(f"{name} ({websocket.remote_address}) connected.")
        await websocket.send(json.dumps({"type": "system", "content": f"Chào mừng {name}! Bạn đã tham gia chat."}))
        await broadcast(f"{name} đã tham gia phòng chat.", sender_websocket=websocket)
        return name # Trả về tên nếu đăng ký thành công

    except websockets.exceptions.ConnectionClosedOK:
        logging.info(f"Client {websocket.remote_address} disconnected before registering.")
        return None
    except (websockets.exceptions.ConnectionClosedError, json.JSONDecodeError, KeyError) as e:
        logging.error(f"Error during registration for {websocket.remote_address}: {e}")
        try:
            await websocket.close(reason="Registration error")
        except: pass # Bỏ qua nếu đóng bị lỗi
        return None
    except Exception as e:
        logging.error(f"Unexpected error during registration: {e}")
        try:
            await websocket.close(reason="Unexpected registration error")
        except: pass
        return None


async def unregister(websocket):
    """Hủy đăng ký client khi họ ngắt kết nối."""
    name = clients.pop(websocket, None)
    if name:
        logging.info(f"{name} ({websocket.remote_address}) disconnected.")
        await broadcast(f"{name} đã rời phòng chat.", sender_websocket=None) # Gửi cho tất cả
    else:
        logging.info(f"Unknown client {websocket.remote_address} disconnected.")


async def handler(websocket, path=None):
    """Xử lý kết nối cho từng client."""
    name = await register(websocket)
    if not name: # Đăng ký thất bại, kết nối đã đóng
        return

    try:
        # Lắng nghe tin nhắn từ client
        async for message_str in websocket:
            logging.info(f"Received raw from {name}: {message_str}")
            try:
                # Giả định client gửi JSON có dạng {"type": "chat", "content": "..."}
                data = json.loads(message_str)
                if data.get("type") == "chat" and "content" in data:
                    message_content = data["content"]
                    # Gửi tin nhắn tới các client khác
                    await broadcast(f"{name}: {message_content}", sender_websocket=websocket)
                else:
                    logging.warning(f"Received invalid message format from {name}: {message_str}")
                    # Có thể gửi lại thông báo lỗi cho client này nếu muốn
                    # await websocket.send(json.dumps({"type": "error", "content": "Invalid message format"}))

            except json.JSONDecodeError:
                logging.warning(f"Received non-JSON message from {name}: {message_str}")
                # Xử lý như tin nhắn text thông thường nếu muốn
                # await broadcast(f"{name}: {message_str}", sender_websocket=websocket)
            except Exception as e:
                logging.error(f"Error processing message from {name}: {e}")

    except websockets.exceptions.ConnectionClosedError as e:
        logging.info(f"Connection with {name} closed unexpectedly: {e}")
    except websockets.exceptions.ConnectionClosedOK:
        logging.info(f"Connection with {name} closed normally.")
    except Exception as e:
        logging.error(f"Unexpected error handling client {name}: {e}")
    finally:
        # Đảm bảo client được hủy đăng ký khi kết nối đóng hoặc có lỗi
        await unregister(websocket)

async def main():
    logging.info(f"Starting WebSocket server on ws://{HOST}:{PORT}")
    # Dùng asyncio.Future() để server chạy mãi mãi cho đến khi bị ngắt (Ctrl+C)
    stop = asyncio.Future()
    async with websockets.serve(handler, HOST, PORT):
        await stop # Chờ Future này hoàn thành (sẽ không bao giờ, trừ khi bị cancel hoặc set_result)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server shutting down...")
    except OSError as e:
        logging.error(f"Could not start server on {HOST}:{PORT}: {e}")
        logging.error("Is the port already in use?")