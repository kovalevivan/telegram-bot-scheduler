# Telegram Bot Scheduler (PuzzleBot scenario runner)

Сервис хранит расписания запусков сценариев и в нужное время вызывает `scenarioRun` для указанного `token + user_id + scenario_id`.

Поддерживается:
- **Ежедневный запуск** в заданное время (с таймзоной).
- **Периодический запуск** с интервалом в минутах.
- **Разовый запуск** в конкретный момент времени.
- **CRUD**: создать/список/редактировать/удалить, а также включить/выключить.
- **Переживает перезапуски**: расписания лежат в базе данных (по умолчанию SQLite).

Запрос, который выполняется воркером:

`https://api.puzzlebot.top/?token=...&method=scenarioRun&scenario_id=...&user_id=...`

## Быстрый старт (без Docker)

1) Создайте окружение и поставьте зависимости:

```bash
python --version  # рекомендуется 3.12 или 3.13
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) (Опционально) создайте `.env`:

```bash
cp .env.example .env
```

По умолчанию используется SQLite файл `./data/app.db`. Для Postgres (если нужно в будущем) задайте `DATABASE_URL`.

3) Запуск:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Проверка:
- Health: `GET http://localhost:8000/health`
- Swagger: `http://localhost:8000/docs`

## API (кратко)

### Создать ежедневную задачу

`POST /schedules/daily`

```json
{
  "token": "BOT_TOKEN",
  "scenario_id": 156490,
  "user_id": 83256012,
  "time_hhmm": "10:30",
  "timezone": "Europe/Moscow"
}
```

### Создать периодическую задачу

`POST /schedules/interval`

```json
{
  "token": "BOT_TOKEN",
  "scenario_id": 156490,
  "user_id": 83256012,
  "every_minutes": 60
}
```

### Создать разовую задачу

`POST /schedules/once`

```json
{
  "token": "BOT_TOKEN",
  "scenario_id": 156490,
  "user_id": 83256012,
  "run_at": "2025-12-17T10:30:00+03:00"
}
```

### Редактировать/включить/выключить

`PATCH /schedules/{id}`

```json
{
  "time_hhmm": "11:00",
  "timezone": "Europe/Moscow",
  "every_minutes": 30,
  "active": true
}
```

### Удалить

`DELETE /schedules/{id}`

## Деплой на Timeweb

Сервис — обычный FastAPI без Docker.

- Build command: `pip install -r requirements.txt`
- Run command: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

Переменные окружения — как в `.env.example` (минимум достаточно дефолтов).
