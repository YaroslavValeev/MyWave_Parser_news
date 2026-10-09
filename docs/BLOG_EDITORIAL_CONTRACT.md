# Blog Editorial Contract (ParserNews)

Локальная копия редакционных правил для раздела Blog на mywavewake.ru.  
Стыковка с Site: [`docs/BLOG_SITE_REFERENCE.md`](BLOG_SITE_REFERENCE.md).

## Эталон

Использовать последний удачный пост на Site как эталон качества.

## Требования

| Поле | Правило |
|------|---------|
| Заголовок (`title` / `raw_title`) | до **90** символов |
| Lead | 1–2 предложения |
| Основной текст (`content_md` / `final_posts`) | 2–5 коротких абзацев |
| Стиль | кратко, содержательно, без повторов, без служебных фраз, без выдуманных деталей |
| Источник | обязательные `source_name` и `source_url` |
| Дата | `published_at` |
| Статус | `READY_TO_PUBLISH` / `PUBLISHED` только после QA |

## Canonical fields (Parser → raw_feed)

| Canonical | raw_feed |
|-----------|----------|
| title | `raw_title` (+ `seo_title` при необходимости) |
| slug | `slug` |
| excerpt | `excerpt` |
| lead | `lead` |
| content_md | `content_md` (без video URL внутри текста) |
| cover_image_url | `cover_image_url` |
| video_url | `video_url` |
| embed_url | `embed_url` |
| video_poster_url | `poster_url` / `video_preview_image_url` |
| media_type | `media_json.type` |
| media_status | `media_status` |
| media_error | `media_error` |
| source_media_url | `source_media_url` |
| tags | `raw_tags` |
| source_name / source_url | `source_name` / `source_url` |
| published_at | `published_at` |
| status | `status` |

## Media statuses

`image_ready` | `video_ready` | `external_video` | `missing` | `failed` | `unsupported`

## Границы

- Parser: качество данных, текст, media fields, source attribution, status, доставка в raw_feed.
- Site: безопасный HTML, video player, CSP, Blog cards, SEO.
- Не трогать: Site Admin, YClients, MyWaveTour Admin, Camp API.
