# 设备服务协议 v2

本文描述已实现接口。资源属于访问的电脑，手机不拥有独立调度副本。全部接口限制在所选局域网接口；可控制的连接必须为 HTTPS。v1 灵动岛视图协议继续存在。

## 认证

浏览器同源访问。`POST /api/v1/pair` 输入 `{"code":"一次性验证码","name":"设备名称"}`；返回 device_id，通过 Secure / HttpOnly / SameSite=Strict Cookie 保存凭据。长期保存只能由电脑操作。

原生客户端先验证服务端证书，再调用 `POST /api/v2/pair`，携带 `X-Solo-Client: native`，不设置 Origin，JSON 结构与浏览器一致。返回 `credential`，仅此原生配对响应包含原始凭据。后续每个请求携带：

```http
X-Solo-Client: native
Authorization: Bearer <安全存储中的凭据>
```

没有 CORS 通配授权。网页不可通过自行增加 native 标志绕过 Cookie 写入保护；native 路径必须携带独立 Bearer 凭据且没有跨站来源。

## 查询接口

- `GET /api/v1/session`：device_id、名称、长期保存状态和临时有效期；浏览器在桌面保存后调用它更新持久 Cookie。
- `POST /api/v1/viewers`：登记当前查看实例。
- `POST /api/v1/presence`：回传服务器 challenge，建立前台在线证据。
- `GET /api/v1/snapshot`：原灵动岛快照，schema_version=1，含 epoch/revision、tasks、外观等。
- `GET /api/v1/events`：SSE，事件为 snapshot、heartbeat、device-saved、session-ended、service-changed。最后一种通知客户端重取 v2 状态，不携带正文。
- `GET /api/v1/assets/{key}`：鉴权后的内容哈希资源，支持 ETag。
- `GET /api/v2/control`：protocol_version=2、server_id、encrypted、grants、csrf、actions、revision、task_revision。
- `GET /api/v2/tasks?offset=0`：任务数组、总数、偏移和目录版本；固定每页最多 50。
- `GET /api/v2/tasks/{id}`：完整任务定义。
- `GET /api/v2/commands/{id}`：当前设备自己提交的操作回执。
- `POST /api/v1/logout`：撤销本设备并清除浏览器 Cookie。

SSE 每次任务更新发送有界完整视图，客户端按 epoch/revision 处理；重连重新取快照，不假设无限事件回放。`service-changed` 只是失效通知，客户端应合并请求并保持编辑草稿。原生客户端可以使用同一 SSE，不能假设一个网络数据块就是一条事件。

## 写入接口

统一使用 `POST /api/v2/commands`，JSON：

```json
{
  "id": "32位小写十六进制唯一命令ID",
  "operation": "save_task",
  "payload": {
    "task_id": "编辑时必填，创建时省略",
    "expected_revision": 1,
    "value": {
      "title": "每周评审",
      "body": "准备演示材料",
      "start_date": "2030-01-01",
      "time_of_day": "10:00",
      "timezone": "Asia/Shanghai",
      "weekdays": [0, 2, 4],
      "interval_hours": 0,
      "lead_minutes": 5,
      "enabled": true
    }
  }
}
```

示例 ID 为说明文字，真实请求必须符合 32 位十六进制。创建时省略 task_id 和 expected_revision；编辑时必须提供最新版本。星期 0–6 对应周一至周日；间隔小时与星期互斥。时间校验复用 TaskStore 调度规则，不能提交过期且不再重复的启用任务。

浏览器写请求必须带 `Content-Type: application/json`、正确 Origin，并携带从 control 取得的 `X-Solo-CSRF`。原生 Bearer 路径不使用浏览器 CSRF，但执行相同业务权限校验。

operation 类型：

- `save_task`：创建、编辑或切换 enabled。编辑提交完整的允许字段，不使用截断摘要。
- `delete_task`：payload 为 task_id、expected_revision。
- `reminder`：使用 actions 目录中的 entity、revision、operation=`local_reminder`，内部 payload 包括 cycle 和 action=`acknowledge` 或 `snooze`。
- `agent`：使用 actions 中的 entity、revision、operation（approve_tool、deny_tool、answer_question、reject_question、stop 等），内部 payload 必须与公布动作对应，包含 confirmation_id、expected_revision 等；不允许客户端直接构造来源路径或任意 run_id。

Agent 示例：

```json
{
  "id": "0123456789abcdef0123456789abcdef",
  "operation": "agent",
  "payload": {
    "entity": "control.actions中目标的id",
    "revision": "control.actions中目标的revision",
    "operation": "approve_tool",
    "payload": {
      "confirmation_id": "公布动作的confirmation_id",
      "expected_revision": "公布动作的expected_revision"
    }
  }
}
```

不支持的动作不会公布。客户端必须展示当前请求详情；不能把 approve_tool 当作通用回答接口。问题答案为数组的数组，受 questions 的选项、单选／多选及 custom 约束。

## 回执与错误

合法业务命令返回 JSON 状态：

- `succeeded`：本地任务事务完成；保存任务同时返回 task_id 和 revision。
- `accepted`：已交给 Agent 来源，实际执行结果继续看状态流。
- `rejected`：冲突、版本变化、权限变化或来源拒绝，没有可宣称的成功。
- `outcome_unknown`：已尝试提交，结果不确定；只能查询回执／实际状态，不自动用新 ID 重试。

客户端必须检查 status，不能仅凭 HTTP 200 显示成功。同一 ID 同一内容返回旧结果；同一 ID 不同内容拒绝。回执按设备隔离，不含任务正文或命令参数。

HTTP 401 表示未认证／已撤销；403 表示来源、CSRF、加密条件或权限不允许；400 表示格式／当前请求版本不满足；404 表示资源不存在；413 表示请求过大；429 表示队列／并发或配对限制；500/503 表示服务不可用。客户端保留草稿、恢复后取最新状态，不盲目重试写入。

## 权限与迁移

grants 包含 view_tasks、edit_tasks、delete_tasks、reminder_act、agent_approve、agent_answer、agent_stop。未设置一律 false。edit_tasks/delete_tasks/reminder_act 自动包含 view_tasks。HTTP 或全局 status_only 模式下，v2 授权全部失效。

协议能力由 control 声明。未来字段应忽略未知可选项，未知主版本拒绝写入。原生 APP 使用安全存储保存凭据，并维护服务器身份和地址；本次没有提供公网转发或发现协议，不能假设任意 IP 地址都是已配对的电脑。
