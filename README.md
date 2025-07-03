# 飞书机器人与Amazon Q CLI集成

本项目将飞书（Lark）消息平台与Amazon Q CLI集成，为飞书群组提供AI驱动的智能对话机器人。

## 🚀 快速部署选项

### 方案一：直接部署代理服务器（推荐）
**最简单的部署方式，只需运行 `app.py` 即可快速上线！**

- ✅ **零云服务依赖** - 直接在本地或服务器运行
- ✅ **快速启动** - 几分钟内完成部署
- ✅ **成本最低** - 无需AWS Lambda或Bedrock费用
- ✅ **易于调试** - 本地运行，日志清晰可见

### 方案二：AWS云原生架构（可选）
使用AWS Lambda + Amazon Bedrock的完整云原生解决方案

- 🔧 **高可用性** - 无服务器架构，自动扩缩容
- 🔧 **企业级** - 适合大规模生产环境
- 🔧 **详细教程** - 参见[AWS官方博客教程](https://aws.amazon.com/cn/blogs/china/amazon-q-developer-cli-and-lark-building-a-conversational-ai-agent-intelligent-platform/)

## ✨ 核心功能

- 🤖 **智能对话** - 基于Amazon Q CLI的AI回复
- 💬 **Thread对话** - 支持在消息线程中连续对话，无需重复@机器人
- 🔄 **流式输出** - 实时显示回复内容，提升用户体验
- 📝 **上下文记忆** - 保持对话历史，支持连续对话
- 🌐 **中文优化** - 强制使用中文回复所有问题
- 🔍 **调试友好** - 提供状态监控和调试端点

## 🏃‍♂️ 快速开始（方案一）

### 1. 环境准备
```bash
# 安装依赖
pip install -r requirements.txt

# 安装并配置Amazon Q CLI
# 参考: https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line-getting-started-installing.html
```

### 2. 配置环境变量
```bash
export LARK_APP_ID=你的飞书应用ID
export LARK_SECRET=你的飞书应用密钥
export PORT=8080  # 可选，默认8080
```

### 3. 启动服务
```bash
# 开发环境
python3 app.py

# 生产环境
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

### 4. 配置飞书Webhook
在飞书开放平台配置Webhook URL：
```
http://你的服务器地址:8080/webhook
```

## 🔧 飞书机器人配置

### 必需权限
在飞书开放平台 -> 权限管理中添加：

| 权限标识 | 说明 | 必需性 |
|---------|------|--------|
| `im:message` | 基础消息权限 | ✅ 必需 |
| `im:message:group_at_msg` | **关键**：接收thread中非@消息 | ✅ 必需 |
| `im:message:group_at_msg:readonly` | 只读消息权限 | ✅ 必需 |

### 事件订阅
添加事件：`im.message.receive_v1`

> **⚠️ 重要提醒**：权限配置后必须重新发布应用版本并重新授权机器人！

## 📊 监控和调试

### 状态检查
```bash
# 查看应用状态
curl http://localhost:8080/status

# 查看调试信息（Thread映射、对话统计等）
curl http://localhost:8080/debug
```

### 日志监控
应用提供详细的日志输出，包括：
- 消息接收和处理过程
- Thread映射建立和更新
- Q CLI调用结果
- 飞书API响应状态

## 🔄 工作原理

1. **接收消息** - 飞书通过Webhook发送用户消息
2. **智能映射** - 自动建立Thread与用户的映射关系
3. **AI处理** - 调用Amazon Q CLI生成回复
4. **流式回复** - 实时发送回复内容到飞书Thread
5. **上下文保持** - 维护对话历史，支持连续对话

## 🆚 部署方案对比

| 特性 | 直接部署app.py | AWS云原生架构 |
|------|---------------|--------------|
| 部署复杂度 | ⭐ 简单 | ⭐⭐⭐ 复杂 |
| 启动速度 | ⚡ 几分钟 | 🕐 需要配置多个AWS服务 |
| 运行成本 | 💰 服务器成本 | 💰💰 Lambda + Bedrock费用 |
| 可扩展性 | 📈 单机限制 | 📈📈📈 无限扩展 |
| 适用场景 | 中小团队、快速验证 | 大型企业、生产环境 |

## 📚 详细教程

如需了解完整的AWS云原生架构实现，请参考：
**[Amazon Q Developer CLI与飞书：构建对话式AI智能平台](https://aws.amazon.com/cn/blogs/china/amazon-q-developer-cli-and-lark-building-a-conversational-ai-agent-intelligent-platform/)**

## 🛠️ 故障排除

### 常见问题
1. **@机器人有回复，Thread中无回复**
   - 检查 `im:message:group_at_msg` 权限
   - 重新发布应用版本

2. **完全收不到消息**
   - 检查基础权限和Webhook配置
   - 查看应用日志

3. **权限配置问题**
   - 使用 `check_permissions.py` 验证配置
   - 确保企业管理员已批准权限申请

### 调试步骤
```bash
# 1. 检查应用状态
curl http://localhost:8080/status

# 2. 查看详细调试信息
curl http://localhost:8080/debug

# 3. 查看应用日志
tail -f app.log  # 如果有日志文件
```

## 📋 快速检查清单

部署前确认：
- [ ] Python 3.8+ 环境
- [ ] Amazon Q CLI 已安装配置
- [ ] 飞书机器人已创建
- [ ] 必需权限已添加
- [ ] 事件订阅已配置
- [ ] 应用版本已发布
- [ ] 环境变量已设置
- [ ] Webhook URL已配置

## 🤝 贡献

欢迎提交Issue和Pull Request来改进这个项目！

## 📄 许可证

本项目采用MIT许可证。
