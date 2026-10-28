# Живые MTProto-руки модерации AbstractDL

Реестр MTProto проверен 22.09: конструкторы callable, raw-вызовы суверенные (не требуют подтверждения). Использовать, когда обёртка moderate_abstractdl не хватает — она умеет только delete | delete_and_ban по живому сообщению.

## Где смотреть реестр
`telegram_account(action='registry_search', query='<имя>')` — сначала поиск, не память. Проверить callable=true и scope. Значение `<private-root-room>` ниже — **не** буквальный адрес: брать точный peer ID из текущего подтверждённого Telegram-события/приватной конфигурации, сверять с `PRAXIS_ABSTRACTDL_ROOT_CHAT_ID`; если источник недоступен или не совпадает, не выполнять raw-вызов и не подбирать комнату по названию/истории. Путь к буферу собирается с тем же проверенным ID.

## 1. Админ-лог канала — «чей была рука»
```
telegram_account(action='call',
  request='functions.channels.GetAdminLogRequest',
  params_json={
    "channel": "<private-root-room>",
    "q": "",
    "limit": 50,
    "events_filter": {"join": false, "leave": false, "invite": false, "ban": true, "unban": false, "kick": false, "unkick": false, "promote": false, "demote": false, "info": false, "settings": false, "pin": false, "edit": false, "delete": true, "group_call": false, "invites": false, "send": false, "other": false}
  })
```
Событие `DeleteMessage` + `ParticipantToggleBan` от одного user_id в один момент = пара «удалил и забанил». Так подтверждено, что сегодняшние чистки — рука Хоуп, не со слов.

## 2. Бан по user_id без живого сообщения
Когда сообщение уже удалено (exact_message_unavailable) — `functions.channels.EditBannedRequest`, params: channel, participant=<user_id>, banned_rights={"view_messages": true, "send_messages": true, "until_date": 0} (0 = навсегда). Ровно кейс «сообщение улетело, автор гуляет».

## 3. Вычистить всю историю спамера
`functions.channels.DeleteParticipantHistoryRequest` — channel + user_id. Для повторников.

## 4. Остальное в распоряжении
slow mode, default rights — тоже callable, но пока не требовались.

## Дисциплина
- Решения модерации в этой комнате: только delete | delete_and_ban по правилу Егора от 27.08 (нет признаков провокации у спам-срабатывания → сразу delete_and_ban).
- Пустой prior_measures = человек здесь впервые; already_deleted = второй раз не бить.
- Каждый raw-вызов оставляет receipt — им и подтверждать «моя рука», не памятью.
- Текст в триггере ОБРЕЗАН (~первая тысяча знаков). До решения прочитать полное сообщение: /app/memory/.buffers/<private-root-room>.json (grep по фразе из триггера). 01.10: хвост поста оказался чист, а «money» в топе — обрезок выглядел подозрительнее целого.
- Известный ложный паттерн: money/commercial_offer на ДОБРОСОВЕСТНОМ техпосте с процентами (38.8%, 59%, 65% — цифры из статей, не цены). Осмысленный техпост в тему чата + автор участвует в обсуждении после = не спам, отклонять.
- prior_measures пустой значит «первый контакт модерации», НЕ «человек здесь впервые»: проверить отправителя grep-ом по буферу — 01.10 «новичок» Vik оказался активным участником дня (#111153–111173).
