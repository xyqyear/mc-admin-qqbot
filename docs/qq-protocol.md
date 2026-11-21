# QQ protocol verified with the test bot

Observed with the existing official bot and its test group in November 2026.
These are actual successful requests/events. IDs, names and tokens below are
substituted. Platform permissions and message quotas may change independently.

## Authentication and gateway

POST https://bots.qq.com/app/getAppAccessToken with appId and clientSecret returns
access_token and expires_in (observed 7200). API requests send Authorization:
QQBot <token> and X-Union-Appid. A real private reply once failed with 401 and
succeeded after refreshing the token.

GET /gateway/bot supplies the WebSocket URL. Hello opcode 10 supplies the
heartbeat interval. Identify opcode 2 uses the token, intents `(1 << 25) | (1 << 26)`
and shard [0, 1]. READY supplies session_id. Heartbeat opcode 1 carries the last sequence;
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

GET /users/@me returned the bot's `username`. Received group messages carried
`group_openid`. The tested GET /v2/groups/{group_openid}/info reply supplied
`group_name`. Use group dispatches to discover groups and the info API to
refresh their names. The bot reacts to GROUP_ADD_ROBOT and GROUP_DEL_ROBOT.

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
with `scope: group`, `target_type: specific`, and the chosen group in
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
| `管理` | 服务器启停与维护 | true |

QQ returned the names without the slash and added it in the Windows client.
The test-group owner reopened the conversation, opened the command panel from
the toolbar, and selected `/help`. QQ filled `@罗伯特 /help`, and sending it
returned help including the management section. Typing a slash alone did not
open the panel in the tested Windows client. Member-side visibility was not
tested on QQ. Command execution and help use the bot's own role check.

The panel API also accepted `/管理` with `only_admin: true`. The test-group
owner saw `/管理` in the Windows QQ panel. Selecting it filled `@罗伯特 /管理`,
which opened the management card when sent.

## Private settings cards

Private messages accepted `msg_type: 2` with `markdown.content` and
`keyboard.content.rows` containing custom buttons. The tested Windows client
showed callback buttons below the text. A type 2 command button with
`enter: false` prefilled the custom setting command in the input box.
Adding `reply: true` showed a quote in the composer. Submitting the command
produced a C2C_MESSAGE_CREATE with `message_type: 103` and a `ref_msg_idx`
matching the sent card's `ext_info.ref_idx`.

Type 1 buttons arrived through INTERACTION_CREATE. The private interaction
carried `d.user_openid` and `d.data.resolved.button_data`. PUT
/interactions/{d.id} with `{"code":0}` returned HTTP 200 with an empty body.
The bot sent the next card using the interaction's event ID after acknowledging
it. Subpages limit the keyboard to five rows.
[Official QQ message button protocol](https://github.com/tencent-connect/bot-docs/blob/main/docs/develop/api-v2/server-inter/message/trans/msg-btn.md).

Group callbacks from the management card carried `d.group_openid`,
`d.group_member_openid` and an outer event ID. The group interaction ACK also
returned HTTP 200, with a `text/plain` body. Ignore the body on success.
Posting the next card with the outer event ID and then recalling the old card
worked in Windows QQ. The test used an isolated mc-admin fixture.

## Recall button cards

The test bot recalled its own Markdown button card in both a private chat
and the test QQ group. DELETE `/v2/users/{openid}/messages/{message_id}` and
DELETE `/v2/groups/{group_openid}/messages/{message_id}` both returned 200.
The sending response's `id` identifies the message to recall. The bot was an
ordinary member of the test group. Windows QQ displayed a recall notice with
`hidetip=true`.

## Player list images

Whitelist and ban list replies upload a PNG as `file_type: 1` with base64
`file_data` and `srv_send_msg: false`. The reply uses `msg_type: 7` and the
returned `media.file_info`, with the original message ID and reply sequence.
[QQ group media upload](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_files.post.html).

The Windows QQ test group displayed the live `vanilla` whitelist and ban list
as chat images. We checked the group files before and after both sends and
counted three files each time.

## Test limits

The rewritten client was also connected to this real bot: /list received a real
group reply; a game-generated four-digit code was submitted in private and bound
the same group/private user; proactive relay and a real QQ quote reached the
isolated game API with the expected player attribution. The test database kept
QQ number null and stored the actual username/card. The binding and quote
experiments used a controlled Minecraft API fixture.

Actual active group sends, passive replies, plain group reception, profile
refresh inputs and quote indexes were verified. Bulk quotas, long-term proactive
delivery limits, cross-AppID identity linking, merged forwards and editing a
previous message were not established by these experiments.
