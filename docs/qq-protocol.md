# QQ protocol verified with the test bot

Observed with the existing official bot and its test group on 2026-11-17.
These are actual successful requests/events. IDs, names and tokens below are
substituted. Platform permissions and message quotas may change independently.

## Authentication and gateway

POST https://bots.qq.com/app/getAppAccessToken with appId and clientSecret returns
access_token and expires_in (observed 7200). API requests send Authorization:
QQBot <token> and X-Union-Appid. A real private reply once failed with 401 and
succeeded after refreshing the token.

GET /gateway/bot supplies the WebSocket URL. Hello opcode 10 supplies the
heartbeat interval. Identify opcode 2 uses the token, intents 1 << 25 and shard
[0, 1]. READY supplies session_id. Heartbeat opcode 1 carries the last sequence;
opcode 11 acknowledges it. Resume uses opcode 6 with session_id and seq.

## Group capabilities

GET /v2/groups/{group_openid}/bot_state actually returned:

```json
{
  "member_openid": "bot-group-openid",
  "joined_at": "2026-11-14T20:22:25+08:00",
  "allow_proactive_msg": true,
  "recv_msg_setting": "all",
  "member_role": "member"
}
```


This member_role describes the **bot**, not the sender. An ordinary unmentioned
message arrived as GROUP_MESSAGE_CREATE. Mentioned messages can also use this
event after full-message reception is enabled. Accept GROUP_AT_MESSAGE_CREATE
as well.

## Identity and profiles

Actual group author fields include id, member_openid, username, avatar, bot,
member_nickname and member_role. Private author fields include id, user_openid,
username, avatar and bot.

For the tested user, member_openid and private user_openid were equal. Matching
is scoped to this AppID. This observation does not establish identity across
different bot applications.

Changing the test user's group card while retaining their QQ nickname changed
member_nickname; username remained the QQ nickname. No numeric QQ number was
provided. union_openid was an empty string; union_user_account was absent.
Persist available author fields, without treating missing union identifiers as
a reliable cross-application mapping.

## Sending

POST /v2/groups/{group_openid}/messages accepted a proactive message containing
only {"msg_type":0,"content":"测试"} and returned HTTP 200. No msg_id/event_id was
required. Passive group and private replies succeeded with incoming d.id as
msg_id and msg_seq 1; private replies use /v2/users/{user_openid}/messages.

The observed send response shape was:

```json
{
  "id": "ROBOT1.0_example",
  "timestamp": "2026-11-17T12:00:00+08:00",
  "ext_info": {"ref_idx": "REFIDX_original"}
}
```


## Quote routing

Quoting that bot message produced GROUP_MESSAGE_CREATE with message_type 103.
Its content included the bot mention, and mentions identified the bot with
is_you:true. The relevant fields were:

```json
{
  "content": "<@bot-group-openid> 引用回复",
  "mentions": [{"id": "bot-group-openid", "is_you": true, "bot": true}],
  "message_type": 103,
  "message_scene": {
    "ext": [
      "ref_msg_idx=REFIDX_original",
      "msg_idx=REFIDX_new"
    ]
  }
}
```


ref_msg_idx exactly matched the successful send's ext_info.ref_idx. This index,
scoped by AppID, conversation type and destination, drives server routing.
The bot's API message id is a separate identifier.

The real message_scene.ext also contained an auth_token. It is deliberately
omitted above and must not be logged or persisted. Other quote metadata included
msg_elements and parallel_message.msg_nodes; neither is needed for routing.

## Group command panel

Use QQ's [create panel API](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels.post.html)
with `scope: group`, `target_type: specific`, and the configured group in
`group_openids`. Configure these items in order with `type: command`.

| Name | Description | `only_admin` |
| --- | --- | --- |
| `list` | 查看在线服务器和玩家 | false |
| `say` | 发消息到游戏，需绑定 | false |
| `whitelist` | 白名单管理（list/add/remove） | false |
| `banlist` | 查看封禁名单 | false |
| `help` | 查看指令用法 | false |
| `ban` | 封禁指定玩家 | true |
| `unban` | 解除玩家封禁 | true |
| `restart` | 重启指定服务器 | true |

QQ returned the names without the slash and added it in the Windows client.
The test-group owner reopened the conversation, opened the command panel from
the toolbar, and selected `/help`. QQ filled `@罗伯特 /help`, and sending it
returned help including the management section. Typing a slash alone did not
open the panel in the tested Windows client. Member-side visibility was not
tested on QQ. Command execution and help use the bot's own role check.

## Test limits

The rewritten client was also connected to this real bot: /list received a real
group reply; a game-generated four-digit code was submitted in private and bound
the same group/private user; proactive relay and a real QQ quote reached the
isolated game API with the expected player attribution. The test database kept
QQ number null and stored the actual username/card. The Minecraft side was a
controlled local API fixture, not a production restart or RCON operation.

Actual active group sends, passive replies, plain group reception, profile
refresh inputs and quote indexes were verified. Bulk quotas, long-term proactive
delivery limits, cross-AppID identity linking, merged forwards and editing a
previous message were not established by these experiments. Lists use plain-text
pages and do not depend on those capabilities.
