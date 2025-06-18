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
            send_lark_request(
                app_id,
                message_id,
                json.dumps({"text": "\n".join(buffer)}),
                reply_in_thread=True
            )
        
        # 如果没有有效输出，返回错误消息
        if not full_response:
            error_msg = "无法获取有效回复"
            send_lark_request(app_id, message_id, json.dumps({"text": error_msg}), reply_in_thread=True)
            return error_msg
        
        # 将助手回复添加到历史
        assistant_response = "\n".join(full_response)
        add_to_history(user_id, "assistant", assistant_response)
            
        return assistant_response
    except subprocess.TimeoutExpired:
        print("q chat 命令执行超时")
        error_msg = "处理请求超时，请稍后再试"
        send_lark_request(app_id, message_id, json.dumps({"text": error_msg}), reply_in_thread=True)
        return error_msg
    except Exception as e:
        print(f"执行 q chat 时发生错误: {e}")
        error_msg = f"执行 q chat 时发生错误: {str(e)}"
        send_lark_request(app_id, message_id, json.dumps({"text": error_msg}), reply_in_thread=True)
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
    # 检查必要参数
    if not app_id or not message_id or not content:
        print("缺少必要参数")
        return {"错误": "缺少必要参数"}

    # 获取应用密钥
    app_secret = APP_ID_SECRET_MAP.get(app_id)
    if not app_secret:
        print(f"未找到应用ID对应的密钥: {app_id}")
        return {"错误": "未找到应用ID对应的密钥"}

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
            print(f"飞书API调用失败: {response.msg}")
            return {"错误": f"{response.msg}"}
        
        return {"成功": True}
    except Exception as e:
        print(f"发送飞书消息时发生错误: {e}")
        return {"错误": str(e)}


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

        # 解析消息内容
        content = json.loads(
            data.get("event", {}).get("message", {}).get("content", "{}")
        )
        # 移除 @at
        message_text = remove_mentions(content.get("text", ""))

        # 发送初始响应，表示正在处理
        send_lark_request(
            app_id, 
            message_id, 
            json.dumps({"text": "正在思考中..."}),
            reply_in_thread=True
        )
        
        # 启动一个后台线程来处理请求，这样可以立即返回响应给飞书
        thread = threading.Thread(
            target=run_q_chat,
            args=(message_text, app_id, message_id, user_id)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({"状态": "成功"})
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
