# Telegram клубы вейк-индустрии — только Москва

Источник OSINT: `telegram_wake_clubs_moscow_osint_2026-11-06.xlsx`  
Фильтр: **город Москва** (без Подмосковья и без смежных медиа).  
Срез: `telegram_wake_clubs_moscow_city_only_2026-11-06.xlsx` / `.csv`

Дата: 2026-11-06

## Включено (12 каналов клубов)

| Клуб | Telegram | Подписчики* | Числовой ID |
|------|----------|-------------|-------------|
| SportStation | [@sportstation_club](https://t.me/sportstation_club) | 1288 | `-1001609224876` |
| Строгино Wake Park | [@WAKEPARKSTROGINO](https://t.me/WAKEPARKSTROGINO) | 1133 | — |
| Wave Hunters | [@wavehuntersclub](https://t.me/wavehuntersclub) | 524 | — |
| Wakestyle Club | [@wakestyleclub](https://t.me/wakestyleclub) | 522* | — |
| GOOD WAVES Moscow | [@GOODWAVEStg](https://t.me/GOODWAVEStg) | 358 | — |
| WakeDivision | [@wakedivision](https://t.me/wakedivision) | 271 | `-1001124621462` |
| Moscow Wake School | [@moscow_wakeschool](https://t.me/moscow_wakeschool) | 210 | — |
| Wakesurfclub Endless Summer | [@wakesurfclubES](https://t.me/wakesurfclubES) | 160 | — |
| WakeShark | [@wakeshark_telegram](https://t.me/wakeshark_telegram) | 144 | — |
| FENIX Wake&Surf School | [@fenixwakeschool](https://t.me/fenixwakeschool) | 133 | — |
| ВейкШанс | [@wakechance](https://t.me/wakechance) | 123 | — |
| River Club Surf | [@riverclubsurf](https://t.me/riverclubsurf) | 122 | — |

\* Подписчики — публичный замер, не live.

## Исключено намеренно

- **Подмосковье:** XWAVE (Долгопрудный), Wakesummer Park, Surf It (Подрезово), Flat Wake Park (Пирогово)
- **Смежные (не клубы Москвы):** Prowakesurf, Wakeboard Teacher, @skiboardSURFING
- Клубы без подтверждённого Telegram / локация МО из листа «на проверку»

## Сбор авторов в Parser

`GROUP_AUTHORS_IDS` по умолчанию = эти 12 `@username` + 2 подтверждённых id.  
Команда: `/collect_group_authors`  
Для broadcast-канала парсер берёт **linked discussion** (кто писал в чат комментариев), не полный roster.
