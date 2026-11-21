# API сценарии

## Создание профиля

```http
POST /api/users
Content-Type: application/json

{
  "name": "Алексей",
  "password": "demo-pass",
  "open_limit": 3,
  "active_kcal_goal": 500
}
```

## Запуск обучения лица

```http
POST /api/enrollment/start
Content-Type: application/json

{"user_id":"usr_xxxxxxxx","device_id":"fridge-001"}
```

После обучения кнопкой `Button-A` на HuskyLens 2 устройство завершает привязку:

```http
POST /api/devices/fridge-001/enrollment/complete
X-Device-Token: demo-device-token
Content-Type: application/json

{"user_id":"usr_xxxxxxxx","face_id":1}
```

## Политика устройства

```http
GET /api/devices/fridge-001/policy
X-Device-Token: demo-device-token
```

Ответ содержит список `face_id`, результат проверки лимитов и гостевой режим. `guest_access` всегда включён для текущего сценария.

## Активные калории

```http
POST /api/health
Content-Type: application/json

{
  "user_id":"usr_xxxxxxxx",
  "date":"2026-11-21",
  "active_calories":640,
  "source":"health_connect"
}
```

При достижении цели сервер добавляет два открытия к дневному лимиту.

## Событие доступа

```http
POST /api/devices/fridge-001/events
X-Device-Token: demo-device-token
Content-Type: application/json

{"face_id":0,"result":"opened","reason":"guest_access"}
```
