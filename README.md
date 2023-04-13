# 🤖 Poe for Home Assistant


<a name="installing"></a>
## Installation

#### Method 1: [HACS (**Click to install**)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hasscc&repository=poe-chat&category=integration)

#### Method 2: Manually install via Samba / SFTP
> [Download](https://github.com/hasscc/poe-chat/archive/main.zip) and copy `custom_components/poe_chat` folder to `custom_components` folder in your HomeAssistant config folder

#### Method 3: Onkey shell via SSH / Terminal & SSH add-on
```shell
wget -q -O - https://hacs.vip/get | HUB_DOMAIN=ghproxy.com/github.com DOMAIN=poe_chat REPO_PATH=hasscc/poe-chat ARCHIVE_TAG=main bash -
```

#### Method 4: shell_command service
1. Copy this code to file `configuration.yaml`
    ```yaml
    shell_command:
      update_poe_chat: |-
        wget -q -O - https://hacs.vip/get | HUB_DOMAIN=ghproxy.com/github.com DOMAIN=poe_chat REPO_PATH=hasscc/poe-chat ARCHIVE_TAG=main bash -
    ```
2. Restart HA core
3. Call this [`service: shell_command.update_poe_chat`](https://my.home-assistant.io/redirect/developer_call_service/?service=shell_command.update_poe_chat) in Developer Tools


## Using

- [![Call service: poe_chat.chat](https://my.home-assistant.io/badges/developer_call_service.svg) `poe_chat.chat`](https://my.home-assistant.io/redirect/developer_call_service/?service=poe_chat.chat)

### Event

- `poe_chat.reply`
- `poe_chat.reply_chunk`
  ```yaml
  event_type: poe_chat.reply
  data:
    name: poe
    bot: capybara
    message: Hello
    id: TWVzc2FnZTozMjM1OTk4Nzc=
    messageId: 323599877
    creationTime: 1681373526812436
    state: incomplete
    text: Hello! How can I assist you today?
    author: capybara
    linkifiedText: Hello! How can I assist you today?
    suggestedReplies: []
    vote: null
    voteReason: null
    text_new: assist you today?
  ```


## Thanks

- https://github.com/ading2210/poe-api
