# Персонажи маскота

Встроенные темы (рисуются кодом): `crab` (по умолчанию), `cat`, `robot`, девочки `girl_sakura` / `girl_mio` / `girl_yuki` и коты `cat_black` / `cat_white` / `cat_gray` (свои пиксель-арты, лицензия проекта). Свои персонажи лежат в
`%APPDATA%\voice-widget\characters\<имя>\` (папку открывает кнопка «Открыть папку ресурсов» в настройках).
Эта папка (`voice-widget/characters/`) тоже сканируется, только для чтения (для готовых наборов).

```
<имя>/character.json                  {"name": "Мой бот", "base": "crab|cat|robot", "fallback": "builtin|idle"}
<имя>/emotions/<эмоция>/anim.gif      GIF (прозрачность поддерживается), fps берется из задержек кадров
<имя>/emotions/<эмоция>/sheet.png     спрайт-лист, размер кадра в emotion.json (frame_w, frame_h), иначе квадраты по высоте
<имя>/emotions/<эмоция>/frames/*.png  последовательность PNG (порядок по именам: 1, 2, 10)
<имя>/emotions/<эмоция>/emotion.json  {"fps": 9, "loop": true, "duration": 0, "frame_w": 32, "frame_h": 32}
```

Эмоции: idle, listening, transcribing, thinking, working, success, error, question, confirm, sleep (+ speaking).
В папке эмоции используется один источник (GIF, лист или кадры). Нет файла или файл битый: берется встроенная
анимация (`base`) или, при `fallback: idle`, картинка idle, в логе `widget.log` строка `[character] ... fallback`.
`duration` > 0: эмоция показывается столько секунд и возвращается в idle (success 3, error 4 по умолчанию).

Состояния диалога -> эмоции (`characters.STATE_TO_EMOTION`, переопределяется в `config.json: emotion_map`):
working -> working, completed -> success, failed -> error, awaiting_clarification -> question,
awaiting_confirmation -> confirm, idle -> idle (через `sleep_after_min` минут покоя -> sleep).
Из кода: `ui.set_emotion('success')` или `ui.set_state('completed')`.

Прозрачность: окно использует цвет-ключ (#FF00FF), края кадров режутся по альфе 50% (без полупрозрачных
полос); пиксели, совпавшие с ключом, сдвигаются на 1 по синему. Плавная полупрозрачность по пикселям не поддерживается.

Модели Live2D / VRM лежат в той же папке персонажей: `character.json` с `"engine": "live2d"|"vrm"`, `"entry"` и папкой `model/` (создаётся импортом в настройках), см. `engines/README.md`.
