# 飞书机器人与Amazon Q CLI集成

本应用将飞书（Lark）消息平台与Amazon Q CLI集成，为飞书群组中的消息提供AI驱动的回复。

## 功能特点

- 通过webhook接收来自飞书的消息
- 使用Amazon Q CLI处理消息
- 支持流式输出，实时显示回复内容
- 在消息thread中回复，不干扰群组其他对话
- 记住对话历史，支持上下文连续对话
- 将回复发送回飞书群组

## 前提条件

- Python 3.8+
- 已安装并配置Amazon Q CLI
- 已创建并配置飞书（Lark）机器人

## 安装步骤

1. 安装依赖：
   ```
   pip install -r requirements.txt
   ```

2. 设置环境变量：
   ```
   export LARK_APP_ID=你的应用ID1,你的应用ID2
   export LARK_SECRET=你的应用密钥1,你的应用密钥2
   export PORT=5000  # 可选，默认为5000
   ```

3. 运行应用：
   ```
   python app.py
   ```

   对于生产环境，使用Gunicorn：
   ```
   gunicorn -w 4 -b 0.0.0.0:5000 app:app
   ```

4. 配置你的飞书机器人使用webhook URL：
   ```
   http://你的服务器地址:5000/webhook
   ```

## 工作原理

1. 飞书向webhook端点发送消息
2. 应用提取消息文本并立即返回"正在思考中..."的初始回复
3. 在后台线程中启动Amazon Q CLI处理消息
4. 随着Q CLI生成回复，应用会分批实时发送到飞书群组
5. 用户可以在飞书中实时看到回复内容，无需等待完整回复

## 注意事项

- 确保服务器上正确安装和配置了Amazon Q CLI
- 应用将用户消息传递给Q CLI并返回响应
- 为了安全，在生产环境中应部署在带有HTTPS的反向代理后面
