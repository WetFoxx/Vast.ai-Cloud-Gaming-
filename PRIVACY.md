vastgame — Privacy Policy
Last updated: September 23, 2026

vastgame is an open-source desktop application that rents a cloud gaming PC on Vast.ai for you and keeps your game saves, Steam session and network settings in your own Google Drive, so they survive between rentals.

The short version
vastgame runs only on your computer. There is no vastgame server, account, analytics or telemetry. The author receives no data from you.
vastgame can access only the Google Drive files it creates itself (the vastai-cloud-games folder). It cannot see any other file in your Drive.
Your keys are stored in your operating system's secure storage (macOS Keychain / Windows Credential Manager) and in your own Vast.ai account.
Google user data
What vastgame accesses. vastgame requests one Google scope: https://www.googleapis.com/auth/drive.file — "access to files created or opened by this app". It does not request access to your other Drive files, email, contacts or profile.

How it is used. vastgame and the rented cloud PC use this access to create, read, update and delete archives inside the vastai-cloud-games folder: game saves, the Steam client session and settings, the cloud PC's network identity (Tailscale, Sunshine), and, if you turn it on, game files. vastgame also lists this folder to show you which games are stored and how much space they take.

Where it goes. Your Google Drive refresh token is stored:

on your computer, in the operating system's secure storage;
in your Vast.ai account, as an environment variable, so each cloud PC you rent can reach your Drive;
on each rented cloud PC while it runs, readable only by its root user.
A rented cloud PC is a machine owned by a third-party host on the Vast.ai marketplace, and the host has administrative access to it. This is inherent to renting a cloud machine — please keep this in mind, and consider using a separate Google account for gaming.

Sharing. vastgame does not sell, share or transfer Google user data to anyone. The data is never sent to the author of vastgame. It is not used for advertising or to train AI models.

vastgame's use and transfer of information received from Google APIs adheres to the Google API Services User Data Policy, including the Limited Use requirements.

Other services vastgame talks to
vastgame sends requests directly from your computer to:

Vast.ai — to search, rent and delete cloud machines and manage your templates and environment variables, using your Vast.ai API key;
Tailscale (the Tailscale app on your computer) — to find your cloud PC in your private network;
Steam Store (store.steampowered.com) — to look up game titles by their Steam app ID; only the app ID is sent;
GitHub — the cloud PC downloads the open-source setup script from the project repository.
Each of these services has its own privacy policy.

Deleting your data and revoking access
Revoke vastgame's access to Google Drive at any time: https://myaccount.google.com/permissions.
Delete the vastai-cloud-games folder in your Google Drive to delete everything vastgame stored there.
Remove the keys from your computer: delete the vastgame entries in Keychain Access (macOS) or Credential Manager (Windows).
Remove RCLONE_REFRESH_TOKEN, TAILSCALE_AUTHKEY and other variables from your Vast.ai account settings.
Contact
Questions and reports: https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/issues

vastgame — политика конфиденциальности (кратко по-русски)
vastgame работает только на вашем компьютере: своего сервера, учётной записи, аналитики и телеметрии нет; автор никаких данных не получает.
Доступ к Google Drive — только к файлам, которые создал сам vastgame (папка vastai-cloud-games), по scope drive.file. Остальные файлы на Drive ему не видны.
Токен Google Drive хранится в защищённом хранилище системы (Keychain / Диспетчер учётных данных), в переменных вашего аккаунта Vast.ai и на арендованной машине, пока она работает. Арендованная машина принадлежит стороннему хосту Vast.ai, у которого есть к ней административный доступ — для игр лучше отдельный аккаунт Google.
Данные Google никому не передаются и не продаются, не используются для рекламы и обучения ИИ. Использование соответствует Google API Services User Data Policy, включая требования Limited Use.
Отозвать доступ: https://myaccount.google.com/permissions. Удалить данные — удалить папку vastai-cloud-games на Drive.
Вопросы: https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/issues
