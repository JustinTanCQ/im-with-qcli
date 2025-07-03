import json
import re
import os
import time
import subprocess
import threading
from typing import Dict, Any, Optional, List, Tuple
from collections import defaultdict
from flask import Flask, request, jsonify

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    ReplyMessageResponse,
)

app = Flask(__name__)

# 对话历史存储
# 格式: {user_id: [(timestamp, role, content), ...]}
conversation_history = defaultdict(list)

# 对话历史过期时间（秒）
CONVERSATION_EXPIRY = 3600  # 1小时

# 对话历史锁，防止并发访问冲突
history_lock = threading.Lock()

# Thread映射相关变量
# 格式: {thread_id: user_id}
thread_user_map = {}
# 格式: {user_id: message_id} - 记录用户最近@机器人的消息ID
user_last_mention_map = {}
# Thread映射锁
thread_map_lock = threading.Lock()

"""
飞书应用 APP_ID 和 SECRET，可支持多应用：
LARK_APP_ID: ID_1,ID_2
LARK_SECRET: SECRET_1,SECRET_2
"""
LARK_APP_ID = os.environ.get("LARK_APP_ID", "").strip()
LARK_SECRET = os.environ.get("LARK_SECRET", "").strip()
APP_ID_SECRET_MAP = {}

if LARK_APP_ID and LARK_SECRET:
    for i, app_id in enumerate(LARK_APP_ID.split(",")):
        secret = LARK_SECRET.split(",")[i].strip()
        APP_ID_SECRET_MAP[app_id] = secret


def remove_mentions(text: str) -> str:
    """移除文本中的@提及"""
    return re.sub(r"\\?@\w+", "", text).strip()


def add_user_mention_record(user_id: str, message_id: str) -> None:
    """
    记录用户@机器人的消息ID，用于后续thread映射
    
    参数:
        user_id: 用户ID
        message_id: 消息ID
    """
    with thread_map_lock:
        user_last_mention_map[user_id] = message_id
        print(f"记录用户@机器人: {user_id} -> {message_id}")


def try_map_thread_to_user(thread_id: str) -> Optional[str]:
    """
    尝试将thread映射到用户，基于最近的@机器人记录
    
    参数:
        thread_id: 线程ID
        
    返回:
        可能的用户ID
    """
    with thread_map_lock:
        # 如果thread已经有映射，直接返回
        if thread_id in thread_user_map:
            return thread_user_map[thread_id]
        
        # 尝试基于时间推断：假设最近@机器人的用户就是这个thread的创建者
        # 这是一个启发式方法，在多用户同时@机器人时可能不准确
        if user_last_mention_map:
            # 简单策略：取最后一个@机器人的用户
            # 在实际应用中，可能需要更复杂的匹配逻辑
            recent_user = max(user_last_mention_map.keys(), 
                            key=lambda u: user_last_mention_map[u])
            
            print(f"启发式映射thread到用户: {thread_id} -> {recent_user}")
            add_thread_user_mapping(thread_id, recent_user)
            return recent_user
        
        return None


def add_thread_user_mapping(thread_id: str, user_id: str) -> None:
    """
    添加线程ID和用户ID的映射关系
    
    参数:
        thread_id: 线程ID
        user_id: 用户ID
    """
    with thread_map_lock:
        old_user = thread_user_map.get(thread_id)
        thread_user_map[thread_id] = user_id
        if old_user != user_id:
            print(f"Thread映射更新: {thread_id} {old_user} -> {user_id}")
        else:
            print(f"Thread映射确认: {thread_id} -> {user_id}")
        
        
def get_user_id_from_thread(thread_id: str) -> Optional[str]:
    """
    根据线程ID获取用户ID
    
    参数:
        thread_id: 线程ID
        
    返回:
        用户ID，如果不存在则返回None
    """
    with thread_map_lock:
        user_id = thread_user_map.get(thread_id)
        print(f"查询Thread映射: {thread_id} -> {user_id}")
        return user_id


def add_to_history(user_id: str, role: str, content: str) -> None:
    """
    添加消息到用户的对话历史
    
    参数:
        user_id: 用户ID
        role: 消息角色 ('user' 或 'assistant')
        content: 消息内容
    """
    with history_lock:
        current_time = time.time()
        # 清理过期的对话
        conversation_history[user_id] = [
            (ts, r, c) for ts, r, c in conversation_history[user_id] 
            if current_time - ts < CONVERSATION_EXPIRY
        ]
        # 添加新消息
        conversation_history[user_id].append((current_time, role, content))
        # 如果历史记录太长，保留最近的10轮对话
        if len(conversation_history[user_id]) > 20:  # 10轮对话 = 20条消息
            conversation_history[user_id] = conversation_history[user_id][-20:]


def get_conversation_context(user_id: str) -> str:
    """
    获取用户的对话历史上下文
    
    参数:
        user_id: 用户ID
        
    返回:
        格式化的对话历史字符串
    """
    with history_lock:
        if not conversation_history[user_id]:
            return ""
        
        # 格式化对话历史
        context = []
        for _, role, content in conversation_history[user_id]:
            prefix = "用户: " if role == "user" else "助手: "
            context.append(f"{prefix}{content}")
        
        return "\n".join(context)


def run_q_chat(message: str, app_id: str, message_id: str, user_id: str) -> str:
    """
    运行 q chat 命令并获取回复，支持流式输出和对话历史
    
    参数:
        message: 用户消息
        app_id: 飞书应用ID
        message_id: 消息ID
        user_id: 用户ID，用于关联对话历史
    """
    try:
        # 获取用户的对话历史
        context = get_conversation_context(user_id)
        
        # 将当前消息添加到历史
        add_to_history(user_id, "user", message)
        
        # 准备发送给Q CLI的完整消息（包含历史上下文）
        full_message = f"请用中文回答以下问题：{message}"
        if context:
            full_message = f"以下是我们之前的对话历史，请基于这个上下文用中文回答我的问题：\n\n{context}\n\n现在，请用中文回答我的问题：{message}"
        
        # 创建一个临时文件来存储消息
        temp_file_path = "/tmp/q_chat_input.txt"
        with open(temp_file_path, "w") as f:
            f.write(full_message)
            f.write("\n/quit\n")  # 添加退出命令以确保 q chat 会话结束
        
        # 使用文件作为输入运行 q chat
        q_process = subprocess.Popen(
            ["q", "chat"],
            stdin=open(temp_file_path, "r"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # 行缓冲
            universal_newlines=True
        )
        
        # 删除临时文件
        try:
            os.remove(temp_file_path)
        except:
            pass
        
        # 用于存储完整回复的变量
        full_response = []
        buffer = []
        last_send_time = time.time()
        
        # ANSI颜色代码正则表达式
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        
        # 逐行读取输出并处理
        for line in iter(q_process.stdout.readline, ''):
            # 清理行（移除ANSI颜色代码）
            cleaned_line = ansi_escape.sub('', line).strip()
            
            # 跳过命令提示符和退出命令
            if cleaned_line.startswith(">") or "/quit" in cleaned_line or not cleaned_line:
                continue
                
            # 添加到缓冲区
            buffer.append(cleaned_line)
            full_response.append(cleaned_line)
            
            # 每隔一定时间或缓冲区达到一定大小时发送更新
            current_time = time.time()
            # 增加缓冲区大小到10行或2秒发送一次，减少消息数量
            if len(buffer) >= 20 or (current_time - last_send_time) >= 4.0:
                if buffer:
                    # 发送缓冲区内容，设置reply_in_thread为True
                    send_lark_request(
                        app_id,
                        message_id,
                        json.dumps({"text": "\n".join(buffer)}),
                        reply_in_thread=True
                    )
                    # 清空缓冲区并更新发送时间
                    buffer = []
                    last_send_time = current_time
        
        # 处理剩余的stderr输出
        stderr = q_process.stderr.read()
        
        # 等待进程结束
        q_process.wait(timeout=5)
        
        if q_process.returncode != 0:
            print(f"q chat 命令执行失败: {stderr}")
            error_msg = f"处理请求时出错: {stderr}"
            send_lark_request(app_id, message_id, json.dumps({"text": error_msg}), reply_in_thread=True)
            return error_msg
        
        # 发送剩余的缓冲区内容
        if buffer:
            final_response = send_lark_request(
                app_id,
                message_id,
                json.dumps({"text": "\n".join(buffer)}),
                reply_in_thread=True
            )
            print(f"最终缓冲区发送结果: {final_response}")
        
        # 如果没有有效输出，返回错误消息
        if not full_response:
            error_msg = "无法获取有效回复，请检查Q CLI是否正常工作"
            print(f"警告: {error_msg}")
            error_response = send_lark_request(
                app_id, 
                message_id, 
                json.dumps({"text": error_msg}), 
                reply_in_thread=True
            )
            print(f"错误消息发送结果: {error_response}")
            return error_msg
        
        # 将助手回复添加到历史
        assistant_response = "\n".join(full_response)
        add_to_history(user_id, "assistant", assistant_response)
        print(f"Q CLI处理完成，回复长度: {len(assistant_response)} 字符")
            
        return assistant_response
    except subprocess.TimeoutExpired:
        error_msg = "处理请求超时，请稍后再试"
        print(f"超时错误: {error_msg}")
        timeout_response = send_lark_request(
            app_id, 
            message_id, 
            json.dumps({"text": error_msg}), 
            reply_in_thread=True
        )
        print(f"超时错误消息发送结果: {timeout_response}")
        return error_msg
    except Exception as e:
        error_msg = f"执行 q chat 时发生错误: {str(e)}"
        print(f"异常错误: {error_msg}")
        exception_response = send_lark_request(
            app_id, 
            message_id, 
            json.dumps({"text": error_msg}), 
            reply_in_thread=True
        )
        print(f"异常错误消息发送结果: {exception_response}")
        return error_msg


def send_lark_request(app_id: str, message_id: str, content: str, reply_in_thread: bool = True) -> Dict[str, Any]:
    """
    发送消息到飞书
    
    参数:
        app_id: 飞书应用ID
        message_id: 要回复的消息ID
        content: 消息内容
        reply_in_thread: 是否在thread中回复，默认为True
    """
    print(f"准备发送飞书消息: app_id={app_id}, message_id={message_id}, reply_in_thread={reply_in_thread}")
    print(f"消息内容: {content[:100]}...")  # 只显示前100个字符
    
    # 检查必要参数
    if not app_id or not message_id or not content:
        error_msg = "缺少必要参数"
        print(f"错误: {error_msg}")
        return {"错误": error_msg}

    # 获取应用密钥
    app_secret = APP_ID_SECRET_MAP.get(app_id)
    if not app_secret:
        error_msg = f"未找到应用ID对应的密钥: {app_id}"
        print(f"错误: {error_msg}")
        return {"错误": error_msg}

    try:
        # 创建飞书客户端
        client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()

        # 构造请求对象
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(content)
                .msg_type("text")
                .reply_in_thread(reply_in_thread)  # 设置是否在thread中回复
                .build()
            )
            .build()
        )

        # 发起请求
        response: ReplyMessageResponse = client.im.v1.message.reply(request)

        # 处理响应
        if not response.success():
            error_msg = f"飞书API调用失败: {response.msg} (code: {response.code})"
            print(f"错误: {error_msg}")
            return {"错误": error_msg}
        
        print(f"飞书消息发送成功: message_id={message_id}")
        return {"成功": True, "response_code": response.code}
    except Exception as e:
        error_msg = f"发送飞书消息时发生异常: {str(e)}"
        print(f"错误: {error_msg}")
        return {"错误": error_msg}


@app.route("/debug", methods=["GET"])
def debug():
    """
    调试端点，显示当前的thread映射和对话状态
    """
    try:
        with thread_map_lock:
            thread_mappings = dict(thread_user_map)
            user_mentions = dict(user_last_mention_map)
        
        with history_lock:
            conversation_stats = {}
            for user_id, messages in conversation_history.items():
                conversation_stats[user_id] = {
                    "message_count": len(messages),
                    "last_message_time": max([ts for ts, _, _ in messages]) if messages else None
                }
        
        debug_info = {
            "timestamp": time.time(),
            "thread_mappings": thread_mappings,
            "user_mentions": user_mentions,
            "conversation_stats": conversation_stats,
            "app_ids": list(APP_ID_SECRET_MAP.keys())
        }
        
        return jsonify(debug_info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/status", methods=["GET"])
def status():
    """
    状态检查端点，用于监控运行状态
    """
    try:
        with thread_map_lock:
            thread_count = len(thread_user_map)
            mention_count = len(user_last_mention_map)
        
        with history_lock:
            history_count = len(conversation_history)
            total_messages = sum(len(messages) for messages in conversation_history.values())
        
        status_info = {
            "status": "running",
            "timestamp": time.time(),
            "thread_mappings": thread_count,
            "user_mentions": mention_count,
            "active_conversations": history_count,
            "total_messages": total_messages,
            "configured_apps": list(APP_ID_SECRET_MAP.keys()),
            "conversation_expiry_hours": CONVERSATION_EXPIRY / 3600
        }
        
        return jsonify(status_info)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    处理飞书webhook请求
    """
    try:
        data = request.json
    except Exception:
        return jsonify({"错误": "无效的JSON数据"}), 400

    # 处理飞书开放平台的 URL 合法性验证请求
    if "challenge" in data:
        challenge = data["challenge"]
        return jsonify({"challenge": challenge})

    # 处理消息事件
    try:
        # 检查消息类型是否为纯文字
        if data.get("event", {}).get("message", {}).get("message_type") != "text":
            app_id = data.get("header", {}).get("app_id", "")
            message_id = data.get("event", {}).get("message", {}).get("message_id", "")
            send_lark_request(
                app_id, 
                message_id, 
                json.dumps({"text": "解析消息失败，请发送文本消息"}),
                reply_in_thread=True
            )
            return jsonify({"状态": "成功"})

        # 提取消息信息
        app_id = data.get("header", {}).get("app_id", "")
        message_id = data.get("event", {}).get("message", {}).get("message_id", "")
        
        # 提取用户ID，用于关联对话历史
        user_id = data.get("event", {}).get("sender", {}).get("sender_id", {}).get("user_id", "")
        if not user_id:
            user_id = data.get("event", {}).get("sender", {}).get("sender_id", {}).get("open_id", "")
        
        if not user_id:
            print("警告: 无法获取用户ID，将使用消息ID作为用户ID")
            user_id = message_id

        # 提取线程ID
        thread_id = data.get("event", {}).get("message", {}).get("thread_id", "")
        
        # 解析消息内容
        content = json.loads(
            data.get("event", {}).get("message", {}).get("content", "{}")
        )
        message_text = content.get("text", "")
        
        # 检查是否在thread中且没有@机器人
        is_in_thread = bool(thread_id)
        has_mention = "@" in message_text
        
        print(f"消息分析: thread_id={thread_id}, has_mention={has_mention}, user_id={user_id}")
        print(f"原始消息内容: {message_text}")
        
        # 如果在thread中且没有@机器人，检查是否是已知的thread
        if is_in_thread and not has_mention:
            thread_user = get_user_id_from_thread(thread_id)
            print(f"Thread用户映射查询: {thread_id} -> {thread_user}")
            
            # 如果没有找到映射，尝试用当前用户建立映射
            if not thread_user:
                print(f"未找到thread映射，尝试建立新映射: {thread_id} -> {user_id}")
                add_thread_user_mapping(thread_id, user_id)
                thread_user = user_id
            
            # 验证用户是否匹配（如果映射存在但用户不同，可能是多用户thread）
            if thread_user != user_id:
                print(f"用户不匹配，更新映射: {thread_id} {thread_user} -> {user_id}")
                add_thread_user_mapping(thread_id, user_id)
                thread_user = user_id
            
            # 处理thread中的消息
            message_text = remove_mentions(message_text)
            print(f"在thread中处理消息: {message_text} (用户: {thread_user})")
            
            # 发送初始响应，表示正在处理
            initial_response = send_lark_request(
                app_id, 
                message_id, 
                json.dumps({"text": "正在思考中..."}),
                reply_in_thread=True
            )
            print(f"初始响应发送结果: {initial_response}")
            
            # 启动一个后台线程来处理请求
            processing_thread = threading.Thread(
                target=run_q_chat,
                args=(message_text, app_id, message_id, thread_user)
            )
            processing_thread.daemon = True
            processing_thread.start()
            
            return jsonify({"状态": "thread中回复", "thread_id": thread_id, "user_id": thread_user})
        
        # 如果有@机器人，处理消息
        if has_mention:
            # 移除@提及
            message_text = remove_mentions(message_text)
            
            print(f"被@机器人，处理消息: {message_text} (用户: {user_id})")
            
            # 记录用户@机器人的行为，用于后续thread映射
            add_user_mention_record(user_id, message_id)
            
            # 如果已经在thread中@机器人，直接记录映射
            if thread_id:
                add_thread_user_mapping(thread_id, user_id)
                print(f"在已有thread中@机器人，记录映射: {thread_id} -> {user_id}")
            else:
                # 首次@机器人，还没有thread_id
                # 机器人回复后飞书会创建thread，用户后续回复时我们再建立映射
                print(f"首次@机器人，用户: {user_id}，等待thread创建")
            
            # 发送初始响应，表示正在处理
            initial_response = send_lark_request(
                app_id, 
                message_id, 
                json.dumps({"text": "正在思考中..."}),
                reply_in_thread=True
            )
            print(f"@机器人初始响应发送结果: {initial_response}")
            
            # 启动一个后台线程来处理请求
            processing_thread = threading.Thread(
                target=run_q_chat,
                args=(message_text, app_id, message_id, user_id)
            )
            processing_thread.daemon = True
            processing_thread.start()
            
            return jsonify({"状态": "@回复成功", "user_id": user_id, "thread_id": thread_id or "新建"})
        
        # 如果既不是在已知thread中，也没有@机器人，则忽略这条消息
        print(f"忽略消息: 不在thread中且未@机器人")
        return jsonify({"状态": "忽略"})
    except Exception as e:
        print(f"处理消息时发生错误: {e}")
        # 仍然返回 200，否则飞书开放平台会反复重试
        return jsonify({"状态": "错误", "消息": str(e)}), 200


if __name__ == "__main__":
    # 设置环境变量
    if not LARK_APP_ID or not LARK_SECRET:
        print("警告: 未设置 LARK_APP_ID 或 LARK_SECRET 环境变量")
    
    # 启动 Flask 应用
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
