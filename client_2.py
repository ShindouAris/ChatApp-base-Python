#!/usr/bin/env python

import asyncio
import websockets
import json
import sys
import os
import logging
from aioconsole import ainput # Sử dụng aioconsole để input không block asyncio

logging.basicConfig(level=logging.INFO)

SERVER_URI = f"ws://127.0.0.1:8765" # Địa chỉ server websocket

stop_flag = asyncio.Event() # Sử dụng Event để báo hiệu dừng

async def receive_messages(websocket):
    """Nhận và hiển thị tin nhắn từ server."""
    try:
        async for message_str in websocket:
            try:
                data = json.loads(message_str)
                content = data.get("content", "N/A")
                msg_type = data.get("type", "chat") # Mặc định là chat nếu không có type

                if msg_type == "system" or msg_type == "error":
                    # Xóa dòng chờ input, in tin nhắn hệ thống/lỗi, in lại dấu nhắc
                    sys.stdout.write('\r\033[K') # Xóa dòng hiện tại
                    print(f"[{msg_type.upper()}] {content}")
                    sys.stdout.write('> ')
                    sys.stdout.flush()
                elif "timestamp" in data:
                    # Tin nhắn chat thông thường
                    timestamp = data.get("timestamp", "")
                    sys.stdout.write('\r\033[K') # Xóa dòng hiện tại
                    print(f"[{timestamp}] {content}")
                    sys.stdout.write('> ')
                    sys.stdout.flush()
                else:
                    # Tin nhắn không xác định rõ định dạng
                    sys.stdout.write('\r\033[K')
                    print(f"[UNKNOWN] {message_str}")
                    sys.stdout.write('> ')
                    sys.stdout.flush()

            except json.JSONDecodeError:
                # Xử lý nếu server gửi không phải JSON (dù không nên xảy ra)
                sys.stdout.write('\r\033[K')
                print(f"[RAW] {message_str}")
                sys.stdout.write('> ')
                sys.stdout.flush()
            except Exception as e:
                logging.error(f"Error processing received message: {e}")
                sys.stdout.write('> ') # In lại dấu nhắc nếu có lỗi
                sys.stdout.flush()


    except websockets.exceptions.ConnectionClosedOK:
        logging.info("Connection closed normally by server.")
    except websockets.exceptions.ConnectionClosedError as e:
        logging.error(f"Connection closed unexpectedly: {e}")
    except Exception as e:
        logging.error(f"Error receiving messages: {e}")
    finally:
        logging.info("Receive loop finished. Signaling stop.")
        stop_flag.set() # Báo hiệu cho luồng gửi dừng lại

async def send_messages(websocket, name):
    """Lấy input từ người dùng và gửi đi."""
    while not stop_flag.is_set():
        try:
            # Sử dụng aioconsole.ainput để không block event loop
            message = await ainput("> ")

            if stop_flag.is_set(): # Kiểm tra lại sau khi await trả về
                break

            if message.lower() == '/quit':
                logging.info("Quitting...")
                break # Thoát vòng lặp, finally sẽ đóng kết nối

            if message:
                # Gửi dưới dạng JSON
                chat_message = {
                    "type": "chat",
                    "content": message
                }
                await websocket.send(json.dumps(chat_message))

        except (EOFError, KeyboardInterrupt):
            logging.info("Input interrupted. Quitting...")
            break # Thoát vòng lặp
        except websockets.exceptions.ConnectionClosed:
            logging.error("Cannot send message: Connection is closed.")
            break
        except Exception as e:
            logging.error(f"Error sending message: {e}")
            # Có thể thêm logic thử lại hoặc thoát tùy trường hợp
            await asyncio.sleep(1) # Tạm dừng nếu có lỗi

    logging.info("Send loop finished. Signaling stop.")
    stop_flag.set() # Đảm bảo báo hiệu dừng nếu vòng lặp kết thúc


async def run_client():
    """Kết nối tới server và chạy các task gửi/nhận."""
    my_name = None
    try:
        async with websockets.connect(SERVER_URI) as websocket:
            logging.info(f"Connected to {SERVER_URI}")

            # === Xử lý đăng ký tên ===
            try:
                # 1. Nhận yêu cầu nhập tên từ server
                name_prompt_str = await websocket.recv()
                name_prompt_data = json.loads(name_prompt_str)
                print(f"[SYSTEM] {name_prompt_data.get('content', 'Server yêu cầu tên.')}")

                # 2. Lấy tên từ người dùng
                while not my_name:
                    _name = await ainput("Nhập tên của bạn: ")
                    my_name = _name.strip()
                    if not my_name:
                        print("Tên không được để trống.")

                # 3. Gửi tên cho server
                await websocket.send(json.dumps({"type": "name_set", "name": my_name}))

                # 4. Nhận phản hồi từ server (chào mừng hoặc lỗi)
                response_str = await websocket.recv()
                response_data = json.loads(response_str)

                if response_data.get("type") == "error":
                    logging.error(f"Server error: {response_data.get('content')}")
                    print(f"Lỗi từ server: {response_data.get('content')}")
                    return # Thoát nếu tên không hợp lệ
                elif response_data.get("type") == "system":
                    print(f"[SYSTEM] {response_data.get('content')}") # In lời chào mừng
                else:
                    print(f"[UNEXPECTED] {response_str}") # Phản hồi lạ

            except (websockets.exceptions.ConnectionClosed, json.JSONDecodeError, EOFError, KeyboardInterrupt) as e:
                logging.error(f"Failed during registration phase: {e}")
                print(f"\nKhông thể hoàn tất đăng ký: {e}")
                return
            # === Kết thúc đăng ký tên ===

            # Chạy song song việc nhận và gửi tin nhắn
            receive_task = asyncio.create_task(receive_messages(websocket))
            send_task = asyncio.create_task(send_messages(websocket, my_name))

            # Chờ cho đến khi có tín hiệu dừng (từ send hoặc receive)
            await stop_flag.wait()

            logging.info("Stop signal received. Cancelling tasks...")

            # Hủy các task còn lại (nếu chúng chưa tự kết thúc)
            for task in [send_task, receive_task]:
                if not task.done():
                    task.cancel()

            # Chờ các task bị hủy hoàn thành (để xử lý CancelledError bên trong chúng)
            await asyncio.gather(send_task, receive_task, return_exceptions=True)

    except websockets.exceptions.InvalidURI:
        logging.error(f"Invalid WebSocket URI: {SERVER_URI}")
        print(f"Lỗi: Địa chỉ server không hợp lệ: {SERVER_URI}")
    except ConnectionRefusedError:
        logging.error(f"Connection refused by the server at {SERVER_URI}.")
        print(f"Lỗi: Không thể kết nối tới server. Server có đang chạy tại {SERVER_URI} không?")
    except (OSError, websockets.exceptions.WebSocketException) as e:
        logging.error(f"Connection failed: {e}")
        print(f"Lỗi kết nối: {e}")
    except Exception as e:
        logging.critical(f"An unexpected critical error occurred: {e}", exc_info=True)
        print(f"Lỗi nghiêm trọng không mong đợi: {e}")
    finally:
        logging.info("Client exiting.")
        print("\nĐã thoát khỏi client.")


if __name__ == "__main__":
    # Cài đặt thư viện aioconsole nếu chưa có: pip install aioconsole
    try:
        asyncio.run(run_client())
    except KeyboardInterrupt:
        logging.info("Client interrupted by user (Ctrl+C).")
        print("\nĐã nhận Ctrl+C, đang thoát...")
        # asyncio.run sẽ tự động dọn dẹp các task khi thoát khỏi context
    # Đảm bảo event loop được đóng sạch sẽ trên Windows
    # asyncio.get_event_loop().close() # Không cần thiết với asyncio.run