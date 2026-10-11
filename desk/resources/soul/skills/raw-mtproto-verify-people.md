# Raw-MTProto рука через registry (telegram_account)

Когда нужен факт из Telegram, которого нет в обычных тулах (статус юзера, канал, saved music, точное имя):
`registry_search` → `describe` (схема параметров) → `call` с `request=functions.<ns>.<Name>Request` — **точное имя конструктора, с префиксом `functions.`**. `users.GetFullUserRequest` без префикса = PolicyError «raw call requires exact constructor name» (4 ложных denied, 2026-10-05). Параметры — плоский JSON (`{"id": 123}`), entity сам разворачивается в InputUser с access_hash.

GetFullUserRequest даёт: username, first/last name, about, онлайн (UserStatusOffline.was_online / Recently), сохранённую музыку (saved_music — включая performer/title, пригодилось опознать Denis_V. @OpenArt), общие чаты, каналы, bot-команды (у Hope: panic/restart/review/evolve/bg). Личные телефоны (Yono) и приватные форварды (NF «NF») — видеть можно, выносить наружу нельзя.

Профильная замена в досье: «не сверялись (окно без Telethon)» → результат сверки с датой и ключевыми фактами. Досье — приватный рантайм (gitignored, canonical на диске), коммит не нужен, авто-бэкап memory/.

Если id человека нигде не зафиксирован (кейс КИТ): честно записать «сверка НЕ сделана: id нет», не изображать знание. Журнал доставки (memory/journal/YYYY-MM-DD.md) — рабочий источник id: Blue нашёлся там строкой «Blue (id=8946264615)».

Слияние досье-дублей: перед merge найти первоисточник спорного факта (claims → life/events/YYYY-MM-DD.jsonl по evidence id). Клод-claims теряли принадлежность голоса: «Denis похвалил мои песни» на деле = Aetheris про свои песни (№30492). После слияния: канон с id + честный факт с источником, старый слаг — редирект-файл, строка в maps/PEOPLE.md переписана, legacy-дубль снят.
