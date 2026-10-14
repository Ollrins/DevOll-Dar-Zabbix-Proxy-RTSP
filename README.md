# 🎥 Zabbix Proxy с мониторингом RTSP-потоков

Готовый Docker-образ **Zabbix Proxy** со встроенным скриптом проверки RTSP-видеопотоков. Подходит для мониторинга IP-камер через Zabbix.

---

## ✨ Возможности

- Проверка RTSP-потока через **OpenCV** или **GStreamer**
- Резервный анализ через **FFprobe**
- Метрики: FPS, яркость, движение, размер кадра, разрешение, задержка
- **Числовой статус** (0/1/2) для триггеров и SLA-отчётов
- **Внутренний таймаут** — скрипт не виснет на неработающих потоках
- Готовый шаблон Zabbix с элементами, триггерами и графиками

---

## 🐳 Установка

### 📦 1. Скачать образ

```bash
docker pull ollrins/zabbix-proxy-rtsp:latest
```

### 📝 2. Пример простого `compose.yml`

> ⚠️ Необходимо создать сеть (если её нет)

```yaml
services:
    mysql:
        image: mariadb:11.4
        container_name: mysql
        restart: unless-stopped
        networks:
            - zabbix-net
        environment:
            MYSQL_DATABASE: zabbix_proxy
            MYSQL_USER: zabbix
            MYSQL_PASSWORD: zabbix
            MYSQL_ROOT_PASSWORD: zabbix

    zabbix-proxy:
        image: ollrins/zabbix-proxy-rtsp:latest
        container_name: zabbix-proxy
        restart: unless-stopped
        depends_on:
            - mysql
        networks:
            - zabbix-net
        environment:
            DB_SERVER_HOST: mysql
            MYSQL_DATABASE: zabbix_proxy
            MYSQL_USER: zabbix
            MYSQL_PASSWORD: zabbix
            MYSQL_ROOT_PASSWORD: zabbix

            # Имя прокси — должно совпадать с именем в веб-интерфейсе Zabbix
            ZBX_HOSTNAME: <PROXY_NAME>

            # IP-адрес вашего Zabbix Server
            ZBX_SERVER_HOST: <ZABBIX_SERVER_IP>

networks:
  zabbix-net:
    external: true
    name: zabbix-net
```

---

## 🔗 Интеграция в Zabbix. Готовый шаблон

Готовый шаблон Zabbix со всеми элементами, триггерами и графиками лежит в репозитории:

**➡️ [`zbx_export_templates.yaml`](https://github.com/Ollrins/Zabbix-Proxy-RTSP/blob/main/zbx_export_templates.yaml)**

---

# 📊 Мониторинг RTSP в Zabbix — Макросы, Метрики и Триггеры

## 🧩 Макросы

| Макрос              | Описание                 | По умолчанию |
|---------------------|--------------------------|--------------|
| `{$CAMERA_LOGIN}`   | Логин от камеры          | `admin`      |
| `{$CAMERA_PWD}`     | Пароль от камеры         | `admin`      |
| `{$CAMERA_PORT}`    | RTSP-порт                | `554`        |
| `{$CAMERA_PATH}`    | Путь RTSP-потока         | `live/main`  |
| `{HOST.CONN}`       | IP-адрес или DNS хоста   | (из интерфейса) |

## 📡 Метрики

| Название                 | Ключ                       | Тип       | Описание                                       |
|--------------------------|----------------------------|-----------|------------------------------------------------|
| RTSP JSON Raw            | `check_rtsp.py[...]`       | EXTERNAL  | Базовый JSON-ответ (мастер-элемент)             |
| RTSP Status Code         | `rtsp.status_code`         | DEPENDENT | Числовой статус: `1`=ok, `2`=no_frames, `0`=error |
| RTSP Is Available        | `rtsp.is_available`        | DEPENDENT | Флаг доступности: `1`/`0`                       |
| RTSP Latency             | `rtsp.latency`             | DEPENDENT | Задержка открытия потока (мс)                   |
| RTSP FPS                 | `rtsp.real_fps`            | DEPENDENT | Кадров в секунду                                |
| RTSP Brightness          | `rtsp.avg_brightness`      | DEPENDENT | Средняя яркость                                 |
| RTSP Motion Level        | `rtsp.frame_change_level`  | DEPENDENT | Средняя разница между кадрами                   |
| RTSP Frame Count         | `rtsp.frames_read`         | DEPENDENT | Сколько кадров получено                         |
| RTSP Frame Size (KB)     | `rtsp.avg_frame_size_kb`   | DEPENDENT | Средний объём кадра                             |
| RTSP Width               | `rtsp.width`               | DEPENDENT | Ширина кадра (px)                               |
| RTSP Height              | `rtsp.height`              | DEPENDENT | Высота кадра (px)                               |
| RTSP Resolution (pixels) | `rtsp.resolution_pixels`   | DEPENDENT | Разрешение в пикселях                           |
| **RTSP Note**            | `rtsp.note`                | DEPENDENT | Диагностика: причина сбоя от скрипта            |

### 🎨 Value Mapping

| Значение | Отображение |
|---|---|
| `1` | Работает |
| `2` | Нет кадров |
| `0` | Недоступна |

### 📝 Про RTSP Note

Текстовое поле для диагностики. Пустое при нормальной работе. Заполняется при сбоях:

| Ситуация | Значение `note` |
|---|---|
| Всё работает | *(пусто)* |
| OpenCV не открыл поток | `OpenCV failed to open stream` |
| OpenCV таймаут | `OpenCV open timed out after 5s` |
| Захват залип | `OpenCV capture timed out after 8s` |
| Подключился, но нет кадров | `Connected, but no valid frames` |
| GStreamer таймаут | `GStreamer open timed out after 5s` |
| ffprobe вытащил метаданные | `... \| Metadata via ffprobe` |

## 🚨 Триггеры

| Название                              | Условие                                                              | Приоритет |
|---------------------------------------|----------------------------------------------------------------------|-----------|
| Чёрный экран                          | `last(avg_brightness) < 5`                                           | Warning   |
| Изображение замёрзло                  | `count(frame_change_level,5m,"lt",1) = 5`                            | Warning   |
| Низкий FPS                            | `last(real_fps) < 5`                                                 | Average   |
| Камера недоступна                     | `last(status_code) = 0`                                              | Average   |
| Камера не выдаёт кадры                | `last(status_code) = 2`                                              | Average   |
| **Проблема с RTSP-потоком**           | `last(status_code) <> 1` **или** `nodata(status_code,5m) = 1`        | **Disaster** |

Триггер **«Проблема с RTSP-потоком»** закрывается только при выполнении **обоих** условий:

```text
last(/RTSP_Camera_Check/rtsp.status_code) = 1
and
nodata(/RTSP_Camera_Check/rtsp.status_code,5m) = 0
```


## ⏱️ Таймаут скрипта

| Параметр | Значение | Где задаётся |
|---|---|---|
| Timeout элемента `RTSP JSON Raw` | **40s** | В шаблоне (`timeout: 40s`) |
| Типовая длительность работы | 4–8 сек | — |
| Худший случай | ~35 сек | срабатывают все три таймаута (OpenCV, GStreamer, ffprobe) |
| Интервал опроса | 60 сек | `delay: 60s` в шаблоне |



## 📈 Графики

| Название                     | Содержимое                                          |
|------------------------------|-----------------------------------------------------|
| Качество видео               | Яркость, FPS, уровень движения                      |
| Доступность камеры           | Status Code, Is Available, Latency                  |
| Разрешение и размер кадра    | Resolution (pixels), Frame Size (KB)                |

---

## 🗂️ Лог-файлы

Внутри контейнера:

| Файл | Назначение |
|---|---|
| `/var/log/check_rtsp.log` | Основной лог: проверки, результаты, таймауты |
| `/var/log/check_rtsp_ffmpeg.log` | Шум ffmpeg/OpenCV |

---

## 📄 Лицензия

MIT License — свободное использование и модификация с сохранением авторства.

---

## 🙌 О проекте

Сделано для мониторинга IP-камер.

Нашли баг или есть идея? Открывайте **Issue**.
Понравилось — поставьте ⭐ репозиторию.
