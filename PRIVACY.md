# vastgame — Privacy Policy

*Last updated: October 30, 2026*

vastgame is a desktop application for Mac and Windows. It rents a cloud gaming PC on Vast.ai for you and keeps your game saves, mods, programs and Steam session in your own Google Drive, so they survive between rentals.

The machine script and the Docker image in this repository are open source. The source code of the desktop application is not published in this repository. The application is distributed under the MIT license.

## The short version

- vastgame runs **only on your computer**. There is no vastgame server, account, analytics or telemetry. The author receives no data from you.
- vastgame can access **only the Google Drive files it creates itself** (the `vastai-cloud-games` folder). It cannot see any other file in your Drive.
- Your keys are stored in your operating system's secure storage (macOS Keychain or Windows Credential Manager) and in your own Vast.ai account.
- If you link Steam accounts, the Steam sign-in token stays in that secure storage on your computer and is sent only to Steam.

## Google user data

**What vastgame accesses.** vastgame requests one Google scope: `https://www.googleapis.com/auth/drive.file` — "access to files created or opened by this app". It does not request access to your other Drive files, email, contacts or profile.

**How it is used.** vastgame and the rented cloud PC use this access to create, read, update and delete archives inside the `vastai-cloud-games` folder: game saves, mods of Steam games, programs you add for a game, the Steam client session and settings, the cloud PC's network identity (Tailscale, Sunshine), games you choose to keep from the cloud PC's Games folder, and, if you turn it on, files that a game's own launcher downloaded. Steam games themselves are downloaded by Steam from Steam's servers, not from your Drive. vastgame also lists this folder to show you which games are stored and how much space they take.

**Where it goes.** Your Google Drive refresh token is stored:

1. on your computer, in the operating system's secure storage;
2. in your Vast.ai account, as an environment variable, so each cloud PC you rent can reach your Drive;
3. on each rented cloud PC while it runs, readable only by its root user.

A rented cloud PC is a machine owned by a third-party host on the Vast.ai marketplace, and the host has administrative access to it. This is inherent to renting a cloud machine. Keep that in mind, and consider a separate Google account for gaming.

**Sharing.** vastgame does not sell, share or transfer Google user data to anyone. The data is never sent to the author of vastgame. It is not used for advertising or to train AI models.

vastgame's use and transfer of information received from Google APIs adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

## Other services vastgame talks to

vastgame sends requests directly from your computer to:

- **Vast.ai** — to search, rent and delete cloud machines and manage your templates and environment variables, using your Vast.ai API key;
- **Tailscale** (the Tailscale app on your computer) — to find your cloud PC in your private network;
- **Steam** — only if you link Steam accounts in the Library. You sign in on Steam's own page shown inside vastgame (QR code or login and password; vastgame never sees your password). vastgame keeps the resulting long-lived Steam sign-in token in your system's secure storage and uses it only to ask Steam (`login.steampowered.com`, `steamcommunity.com`, `api.steampowered.com`) for the games you own and your Family Library. Game titles and artwork are looked up in the Steam Store by app ID. None of this is sent anywhere else;
- **GitHub** — the cloud PC downloads the setup script from this repository (and, in Docker mode, the desktop image from GitHub Container Registry, `ghcr.io`). vastgame checks the project's Releases for a newer version of itself. That check is a plain download of a small file: GitHub sees your IP address, and nothing else is sent. When you choose to update, the new version is downloaded from there.

Each of these services has its own privacy policy.

## Deleting your data and revoking access

- Revoke vastgame's access to Google Drive at any time: <https://myaccount.google.com/permissions>.
- Delete the `vastai-cloud-games` folder in your Google Drive to delete everything vastgame stored there.
- Unlink Steam accounts in vastgame → Settings → Steam accounts. The Steam token is then deleted from your computer.
- Remove the keys from your computer: delete the `vastgame` entries in Keychain Access (macOS) or Credential Manager (Windows), or use Settings → Reset VastGame on this Mac (on Windows: Reset VastGame on this computer). Reset clears keys and settings on that computer. It does not delete your Google Drive folder, your Vast account or the template.
- Remove `RCLONE_REFRESH_TOKEN`, `TAILSCALE_AUTHKEY` and other variables from your Vast.ai account settings.

## Contact

Questions and reports: <https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/issues>

---

# vastgame — политика конфиденциальности

*Обновлено 30 октября 2026. Полный текст выше — на английском. Здесь то же самое по-русски.*

vastgame — программа для Mac и Windows. Она арендует игровой компьютер на Vast.ai и хранит сохранения, моды, программы и сессию Steam в вашем Google Drive, чтобы они переживали смену машины. Скрипт машины и образ Docker в этом репозитории открыты. Исходный код самой программы здесь не опубликован. Программа распространяется по лицензии MIT.

- Программа работает **только на вашем компьютере**. Своего сервера, учётной записи, аналитики и телеметрии нет. Автор никаких данных не получает.
- Доступ к Google Drive — один scope, `drive.file`: **только файлы, которые создала сама программа** (папка `vastai-cloud-games`). Остальные файлы на Drive ей не видны. Почта, контакты и профиль не запрашиваются.
- В этой папке лежат сохранения, моды игр Steam, программы, которые вы добавили к игре, сессия и настройки Steam, сетевая личность машины (Tailscale, Sunshine), игры из папки Games, которые вы решили хранить, и, если вы включили это отдельно, файлы, которые докачал собственный лаунчер игры. Сами игры Steam качает Steam, не Drive.
- Токен Google Drive хранится в защищённом хранилище системы (Связка ключей macOS или Диспетчер учётных данных Windows), в переменных вашего аккаунта Vast.ai и на арендованной машине, пока она работает. Машина принадлежит стороннему хосту Vast.ai, и у владельца хоста есть к ней административный доступ. Для игр лучше отдельный аккаунт Google.
- Если привязать аккаунты Steam, вход идёт на странице самой Steam. Пароль программа не видит. Пропуск Steam хранится в защищённом хранилище и отправляется только в Steam, чтобы узнать список ваших игр и игр семьи.
- Данные Google никому не продаются и не передаются, в том числе автору. Они не используются для рекламы и для обучения ИИ. Использование соответствует [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), включая требования Limited Use.
- Отозвать доступ: <https://myaccount.google.com/permissions>. Удалить данные — удалить папку `vastai-cloud-games` на Drive. Сброс в настройках программы стирает ключи на этом компьютере и не трогает папку на Drive.
- Вопросы: <https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/issues>

---

# vastgame — política de privacidad

*Actualizado el 30 de octubre de 2026. El texto completo de arriba está en inglés. Aquí está lo mismo en español.*

vastgame es un programa para Mac y Windows. Alquila un PC para jugar en Vast.ai y guarda las partidas, los mods, los programas y la sesión de Steam en tu Google Drive, para que sobrevivan al cambio de máquina. El script de la máquina y la imagen Docker de este repositorio son abiertos. El código fuente del programa de escritorio no está publicado aquí. El programa se distribuye bajo la licencia MIT.

- El programa funciona **solo en tu ordenador**. No tiene servidor propio, ni cuenta, ni analítica, ni telemetría. El autor no recibe ningún dato tuyo.
- El acceso a Google Drive es un solo alcance, `drive.file`: **solo los archivos que creó el propio programa** (la carpeta `vastai-cloud-games`). No ve el resto de tu Drive. No pide correo, contactos ni perfil.
- En esa carpeta están las partidas, los mods de juegos de Steam, los programas que añadiste a un juego, la sesión y los ajustes de Steam, la identidad de red de la máquina (Tailscale, Sunshine), los juegos de la carpeta Games que decidiste guardar y, si lo activaste aparte, los archivos que descargó el launcher propio del juego. Los juegos de Steam los descarga Steam, no Drive.
- El token de Google Drive se guarda en el almacenamiento seguro del sistema (Llavero de macOS o Administrador de credenciales de Windows), en las variables de tu cuenta de Vast.ai y en la máquina alquilada mientras está en marcha. La máquina pertenece a un host de Vast.ai, y el dueño del host tiene acceso de administrador. Para jugar conviene una cuenta de Google aparte.
- Si vinculas cuentas de Steam, el acceso es en la página del propio Steam. El programa no ve la contraseña. El pase de Steam se guarda en el almacenamiento seguro y se envía solo a Steam, para conocer la lista de tus juegos y los de la familia.
- Los datos de Google no se venden ni se ceden a nadie, tampoco al autor. No se usan para publicidad ni para entrenar modelos de IA. El uso cumple la [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), incluidos los requisitos de Limited Use.
- Retirar el acceso: <https://myaccount.google.com/permissions>. Borrar los datos: borrar la carpeta `vastai-cloud-games` en Drive. El restablecimiento en los ajustes del programa borra las claves de ese ordenador y no toca la carpeta de Drive.
- Preguntas: <https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/issues>
