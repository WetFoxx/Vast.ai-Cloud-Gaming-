# VastGame

[English](README.md) · **Español** · [Русский](README.ru.md)

> **Beta.** Ya hay gente usándola, y todavía cambia. Si algo se rompe, escríbelo en [Issues](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/issues).

VastGame sirve para jugar en una máquina alquilada de [Vast.ai](https://vast.ai) sin montarla de nuevo cada vez. Pulsas un juego de tu biblioteca de Steam y se abre la lista de máquinas. La máquina la eliges tú y confirmas el precio. Después del alquiler, Steam en la máquina descarga el juego y lo inicia, y Moonlight se abre solo. Las partidas, la sesión de Steam, los mods y los programas quedan en tu Google Drive y vuelven en la siguiente máquina. «Terminar» sube todo eso primero y solo entonces borra la máquina. Mientras no hay máquina, no se cobra nada.

El programa elige otra máquina por su cuenta solo cuando la que elegiste no arranca. Entonces la borra y toma la siguiente de tu lista que no sea peor en tarjeta, precio y ping. Mientras la máquina elegida arranca con normalidad, no se sustituye por otra.

![Pantalla principal de VastGame](docs/home-es.jpg)

**[Descargar para Mac](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/releases/latest/download/vastgame-mac.dmg)**
(Apple Silicon, M1 o posterior)
·
**[Descargar para Windows](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/releases/latest/download/VastGame-Setup.exe)**
(Windows 10/11, 64 bits)

Las compilaciones todavía no están firmadas por Apple ni por Microsoft, así que el sistema pide permiso la primera vez. Cómo darlo está en «Instalación».

## Cómo va una sesión

Primero va la configuración. El programa pide, paso a paso, la clave de Vast.ai, el acceso a Google y Tailscale. Steam, Moonlight y Steam Link los instala él mismo si todavía no están.

Entras en Steam en la página del propio Steam, con un código QR o con usuario y contraseña. VastGame no ve la contraseña. Puedes vincular varias cuentas, incluida una biblioteca familiar.

Al pulsar un juego en la estantería o en la biblioteca se abre la lista de máquinas. En la ficha del juego, antes de alquilar, eliges «Con mods» o «Sin mods». «Máquina sin juego» alquila el escritorio y no inicia ningún juego.

Después eliges la máquina y confirmas el precio. El programa la alquila, devuelve lo que hay en la nube, entra en la cuenta de Steam que toca, y Steam descarga e inicia el juego. Solo funciona una máquina a la vez. Otro juego en una máquina que ya está en marcha lo abres en Steam desde Moonlight. Para tomar otra máquina, pulsa primero «Terminar».

## Biblioteca

En la pantalla principal hay una estantería con los últimos juegos. «Biblioteca completa» muestra todos los juegos de las cuentas vinculadas, aunque el perfil de Steam esté cerrado. Los juegos de la familia van marcados, y hay búsqueda y filtro por cuenta.

En la ficha del juego se ve con qué cuenta se iniciará, si los mods están activos y qué hay ya en la nube. Si el acceso a Steam ha caducado, la lista de juegos se queda, y en la cuenta aparece que hay que entrar de nuevo.

## Lista de máquinas

En cada máquina de la lista se ven la tarjeta, el precio por hora, el país, el ping y la velocidad de internet. Los filtros y el orden se recuerdan. El disco se elige con un control de 50 a 1000 GB, y el precio por hora ya incluye ese tamaño.

La pestaña Docker está abierta por defecto. Hay más máquinas, y normalmente están listas en uno a tres minutos. El mando no vibra, y Vast cobra aparte el tráfico de la transmisión. El filtro «Tráfico» deja los hosts en los que ese tráfico sale barato.

KVM hace falta si quieres vibración o un tráfico casi gratis. Ahí hay pocas máquinas, y el arranque suele tardar de cinco a diez minutos.

Hasta que no has jugado en una máquina concreta, el ping se muestra aproximado, por la distancia de tu ciudad a la región del host. Después de la primera partida el programa recuerda una medición real por Tailscale y a partir de ahí muestra esa.

Si Vast no levanta la máquina elegida en diez minutos, o en Docker no arrancan la pantalla o el codificador, el programa la borra y toma la siguiente que sirva de tu lista. Si no hay ninguna, vuelves a la lista. Cancelar el arranque borra la máquina si ya estaba alquilada.

## La transmisión

La resolución por defecto es la nativa de tu monitor, y la tasa de bits está en automático: la calcula Moonlight. Los dos ajustes están en «Ajustes de transmisión», debajo del botón de inicio, y se recuerdan.

Al lado está Steam Link. La primera vez que esa cuenta no está vinculada a tu ordenador, se abre la ventana de Steam Link y un campo para cuatro cifras. VastGame envía el código a Steam en la máquina y guarda el vínculo en la nube. Si no tienes el cliente, en Mac se abre la App Store, y en Windows el programa instala Steam Link él mismo.

Por la ventana de VastGame se puede mover uno con el mando y las flechas. Mientras Moonlight levanta el flujo, la pantalla queda cubierta por la ficha del juego.

## Mods

A la nube no va el juego en sí. Va la diferencia entre su carpeta y una instalación limpia de Steam: lo que añadiste, cambiaste o borraste mientras el juego estaba cerrado. Los registros y la caché no se llevan. El juego lo vuelve a descargar Steam en cada máquina.

Hay un solo interruptor, y solo se puede cambiar antes de alquilar. Durante la sesión está cerrado y dice que se cambia antes de alquilar.

Con «Con mods», antes de iniciar se aplica el archivo de la nube sobre el juego limpio. Lo que cambie en la carpeta después de salir del juego se guarda y vuelve en la siguiente máquina.

Con «Sin mods» arranca una instalación limpia de Steam. El archivo de la nube se queda donde está, pero no se aplica al juego, y los mods nuevos de esta sesión no se guardan. Las partidas sí se suben como siempre.

Para poner mods, elige «Con mods» y alquila una máquina. Espera a que Steam instale el juego y ciérralo. Mientras está en marcha es mejor no tocar la carpeta, porque la foto del juego limpio se toma justo después de la instalación. Los archivos van a la carpeta del juego. Puedes descargarlos con Chrome en el escritorio de la máquina. El perfil del navegador, las contraseñas y los marcadores no se suben. Cuando sales del juego o pulsas «Terminar», los mods van a Google Drive. La próxima vez Steam vuelve a descargar el juego limpio y VastGame pone los mods encima. En la pantalla principal, en ese momento, dice que está aplicando los mods.

Mod Organizer, Vortex y los demás gestores se instalan como programas, no como archivos de un mod. Van en la sección siguiente.

Si Steam actualiza el juego en la misma sesión, los mods puestos ahora no se suben. Así los archivos viejos no caen sobre la versión nueva. En un juego que ya está actualizado se guardan con normalidad.

Los mods se quitan en «Juegos en la nube», en el grupo de mods, con «Quitar». El archivo permanece treinta días en la papelera de Google Drive y se puede recuperar. El juego y las partidas se quedan.

## Programas

Aquí entran trainers, WeMod, Mod Organizer, SKSE, Mod Engine, XIVLauncher y todo lo que deba abrirse al lado del juego o en su lugar.

De dónde pongas el programa depende si volverá en la siguiente máquina.

| Dónde lo pones | Qué se guarda |
|---|---|
| En la carpeta Programs del escritorio, cada programa en su carpeta | Se sube siempre y estará en la siguiente máquina |
| Directamente en la carpeta del juego | Se guarda con los mods, y solo si juegas «Con mods» |
| Con el botón «Instalar en el juego» | Queda en el prefijo Proton y se guarda con las partidas |

La máquina tiene que estar en marcha y el juego ya descargado. Descarga el archivo con Chrome en el escritorio y haz doble clic en el programa, el archivo comprimido o el instalador. VastGame reconoce el juego, pone el archivo en Programs o lo instala en el prefijo, y pregunta si hay que iniciarlo junto con el juego. También se puede hacer a mano: crea una carpeta dentro de Programs y mete ahí el programa. En aproximadamente medio minuto aparece en «Programas del juego», en la ficha. Si la lista está vacía, ahí mismo dice dónde poner los archivos.

«Iniciar» abre el programa en la misma sesión de Moonlight. Se cambia a su ventana con Alt+Tab. Un trainer necesita que el juego ya esté en marcha. Un gestor de mods puede arrancar en el prefijo aunque el juego esté cerrado.

«Con el juego» hace que se inicie junto con el juego, en esta máquina y en las siguientes.

«Inicio del juego» hace que Steam inicie el programa elegido en lugar del exe del juego. Así arrancan SKSE, Mod Engine y XIVLauncher. El Proton, las partidas y la cuenta siguen siendo los mismos. Funciona en el modo «Con mods», y la elección se recuerda. Mientras el juego ya está en marcha no se puede reiniciar Steam, así que la forma nueva entra después de salir del juego o en la siguiente máquina.

«Instalar en el juego» ejecuta el instalador una vez dentro del Proton de ese juego. El juego tiene que estar cerrado y ya instalado en Steam. La ventana del instalador se ve en Moonlight. Si después el juego debe arrancar a través de ese programa, elígelo en «Inicio del juego». Lo instalado se guarda con las partidas.

En la ficha de la versión gratuita de FINAL FANTASY XIV hay un interruptor aparte, «Traducción al ruso». Cuando está activo, la máquina instala XIVLauncher, Dalamud, Penumbra y la traducción, y en los alquileres siguientes actualiza la traducción si sale una versión nueva. El juego se inicia con XIVLauncher. En la cuenta de Square Enix entras tú. Todo eso se guarda con las partidas. Cuando el interruptor está apagado, el juego va con el launcher normal de Square Enix, y una traducción que ya esté en la nube no se borra.

Al lado está «Guardar el juego en la nube». Conviene activarlo en juegos con su propio launcher, como FF14 y otros MMO. Entonces los archivos que descargó el launcher llegan a la siguiente máquina y no hay que bajar el juego otra vez. A un juego normal de Steam ese interruptor no le aporta nada. Los mods, en cualquier caso, se guardan aparte.

## Juegos que no son de Steam

En el escritorio de la máquina hay una carpeta Games. Ahí se pone un juego comprado fuera de Steam. Cuando aparece una carpeta nueva, VastGame pregunta por encima de todas las ventanas si hay que llevársela a la nube. Si respondes que sí, se sube a Google Drive y se descarga en las máquinas siguientes. Si respondes que no, se queda solo en esta máquina y desaparece con ella. Si no respondes, un juego nuevo se sube igual. Lo que ya está en la nube no se borra solo porque quites la carpeta en la máquina. Se quita en «Juegos en la nube», y otra vez son treinta días en la papelera de Drive.

## Partidas guardadas

Las partidas se van a la nube tanto mientras juegas como cuando pulsas «Terminar». Entran las de Steam y los archivos que el juego escribe en su carpeta. En la siguiente máquina ya están ahí antes de iniciar.

Una partida se puede borrar en «Juegos en la nube». Si no hay copia en Steam Cloud, el juego no la devuelve, y el programa avisa de eso aparte.

Si cierras sesión de una cuenta de Steam en la propia máquina, sus datos locales salen de la sesión en la nube y no vuelven a la siguiente máquina. Los archivos de partidas de los juegos no se tocan.

## Cuando terminas

«Terminar» cierra el juego, manda a la nube todo lo que debe guardarse, espera la confirmación y solo entonces borra la máquina. Si la subida no se confirma, la máquina se queda y Vast sigue cobrando.

Si el Mac se apaga o se cae internet después de haber pulsado, la máquina termina la subida sola y se borra ella misma. Cuando vuelve la conexión, VastGame muestra que la sesión ya está cerrada.

«Borrar sin subir» está en los ajustes de la transmisión y pregunta dos veces. La máquina se borra al momento, y lo que todavía no había llegado a la nube se pierde.

## Qué hay en la nube

«Juegos en la nube» muestra lo que ya hay en tu Google Drive y cuánto ocupa. Ahí están los juegos, las partidas, los mods, los programas y los archivos de los launchers. Los archivos de servicio de Steam, como Proton y las bibliotecas compartidas, van plegados aparte. Hacen falta para que los juegos arranquen. Si los borras, Steam los descarga otra vez.

«Quitar» no borra el archivo al momento. Treinta días permanece en la papelera de Drive. Si hay una máquina en marcha, el borrado pasa por ella, para que no vuelva a subir el archivo. Si no hay máquina, el archivo va directo a la papelera.

## Qué hace falta

Hace falta un Mac con Apple Silicon, de M1 en adelante, o Windows 10/11 de 64 bits. Hace falta una cuenta de [Vast.ai](https://vast.ai) con dinero en el saldo. Para empezar bastan 5–10 dólares. Hace falta una cuenta de Google, adonde irán las partidas, los mods y los programas. Una cuenta aparte para los juegos es más cómoda que la principal. Hace falta [Tailscale](https://tailscale.com). Es gratis, y se puede entrar con Google. Tu propia clave no es obligatoria. La primera máquina la apruebas una vez, desde un enlace en el navegador, y después se levanta desde la identidad guardada. Conviene un internet de 30–50 Mbit/s o más, y tiene sentido mantener el ping por debajo de 80–100 ms.

«Recargar» solo abre el pago de Vast. El programa no ve la tarjeta y no cobra nada por su cuenta.

## Cuánto cuesta

El programa en sí es gratis. Pagas al dueño de la máquina en Vast.ai por el tiempo de alquiler. Los precios los ponen los dueños, y ahora vienen a ser estos.

| Tarjeta | Precio por hora |
|---|---|
| RTX 3060 | $0.07–0.15 |
| RTX 3080 | $0.13–0.25 |
| RTX 4090 | $0.40–0.60 |

En la pestaña Docker al precio por hora se le suma el tráfico. Lo que puede costar ese tráfico lo limita el filtro «Tráfico». Lo que se cobró de verdad está en «Historial», según las facturas de Vast.

## Instalación

En Mac, descarga [vastgame-mac.dmg](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/releases/latest/download/vastgame-mac.dmg), ábrelo y arrastra VastGame a Aplicaciones. No hay firma de Apple, así que el sistema no deja abrir el programa la primera vez. Abre Ajustes del Sistema, entra en Privacidad y seguridad y, abajo, pulsa «Abrir de todas formas». Hace falta una sola vez. Después pasa la configuración en la ventana.

En Windows, descarga [VastGame-Setup.exe](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/releases/latest/download/VastGame-Setup.exe) y ejecútalo. SmartScreen avisará de un editor desconocido. Pulsa «Más información» y luego «Ejecutar de todas formas». WebView2 en Windows 11 normalmente ya está instalado. La configuración a partir de ahí es la misma que en Mac.

Después el programa se actualiza solo. En la pantalla principal aparece que hay una versión disponible, y al lado el botón de actualizar. El archivo se comprueba con una firma, y una actualización ajena no se instala.

## Datos

VastGame no tiene servidor propio ni recoge estadísticas. Todo se ejecuta en tu ordenador. Las claves están en el Llavero de macOS o en el Administrador de credenciales de Windows. En Google Drive el programa solo ve la carpeta que creó él mismo.

Una máquina alquilada es el ordenador de otra persona, y el dueño del host tiene acceso mientras está en marcha. No dejes ahí nada personal. Para jugar es más tranquilo usar un Google aparte y, si no quieres usar el principal, un Steam aparte.

Más detalle está en la [política de privacidad](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/blob/main/PRIVACY.md).

## Licencia

Los archivos de este repositorio se distribuyen bajo la licencia [MIT](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/blob/main/LICENSE). Son el script de la máquina, la imagen Docker y estos textos. El programa para Mac y Windows se distribuye en las mismas condiciones, pero su código fuente no está en el repositorio. La licencia permite usarlo, copiarlo y modificarlo. El programa y el script se dan tal cual, sin garantía.

---

---

## El script en la máquina

A continuación va la guía que ya estaba, de `wolf-setup.sh`, para quien alquila la máquina a mano, sin el programa.

# wolf-setup.sh

[English](README.md) · **Español** · [Русский](README.ru.md)

Un PC para jugar en la nube, sobre una GPU alquilada en Vast.ai, que no desaparece junto con la instancia.

Alquilas una máquina, juegas y la destruyes. La próxima vez —en otra máquina, quizá una semana después— Moonlight se conecta a la misma dirección, Steam sigue con la sesión iniciada y tus partidas guardadas están donde las dejaste. El script convierte una instancia KVM recién creada en un escritorio con Steam y Sunshine, la une a tu red de Tailscale con un nombre fijo, restaura desde Google Drive tu sesión de Steam, la configuración, los prefijos de Proton y las partidas guardadas, y sigue subiendo los cambios mientras juegas.

```
   Vast.ai (KVM, NVIDIA)                  Google Drive            tus dispositivos
┌─────────────────────────┐          ┌──────────────────┐      ┌──────────────────┐
│  Steam + Proton         │◄────────►│  identity        │      │  Moonlight       │
│  Sunshine ──────────────┼──────────┼─ steam-state     │      │  + Tailscale     │
│  wolf (sincronización)  │          │  steam-cache     │      │  (teléfono, PC,  │
│  Tailscale ─────────────┼──────────┼─ pfx--<appid>    │      │   portátil, TV)  │
└──────────┬──────────────┘          │  game--<carpeta> │      └────────▲─────────┘
           │                         └──────────────────┘               │
           └────────── Tailscale (todo el tráfico del stream) ──────────┘
```

---

## Para quién es

Esto no es un servicio de un solo clic. Vas a crear cuentas en tres sitios, copiar dos claves en la configuración de Vast y, de vez en cuando, abrir una terminal en la instancia. Si alguna vez montaste Plex, un servidor casero o un escritorio Linux, te las arreglarás. Si solo quieres pulsar «Jugar», GeForce NOW y Boosteroid están hechos para eso.

Lo que obtienes a cambio: tu propia biblioteca de Steam, mods y juegos que no son de Steam, en una GPU que pagas por horas y solo mientras juegas.

---

## Qué hace

- **Una dirección fija.** El estado del nodo de Tailscale se guarda en la nube, así que cada instancia nueva arranca como el mismo nodo: mismo nombre, misma IP. Añades el PC a Moonlight una sola vez.
- **Steam sigue con la sesión iniciada.** Se conservan `machine-id`, `loginusers.vdf`, `ConnectCache`, `ssfn*` y `userdata/`, así que la sesión sobrevive al cambio de máquina. El modo sin conexión también funciona, porque la caché de licencias (`appcache`) se sube a la nube igualmente.
- **Sincronización incremental.** Solo se sube lo que cambió. Una partida guardada es un archivo de unos pocos kilobytes, no una resubida del prefijo entero.
- **Subida en cuanto sales del juego.** Un servicio en segundo plano detecta cuándo se cierra un juego, sube sus partidas guardadas en unos 10 segundos y muestra una notificación en el escritorio.
- **La resolución se adapta a tu dispositivo.** El escritorio arranca a 1920×1200 en una GPU sin monitor conectado, cambia a la resolución nativa de tu dispositivo cuando Moonlight se conecta y vuelve a la base al desconectarte.
- **Aislamiento.** Sunshine y la página de estado solo aceptan conexiones a través de Tailscale. Los secretos nunca llegan a los juegos ni a la lista de procesos.

---

## ¿Por qué no mantener un disco en Vast?

Vast cobra el almacenamiento por cada hora que existe la instancia, esté en marcha o detenida: normalmente entre $0.09 y $0.20 por GB al mes, y algunos hosts bastante más. Un disco de 500 GB para una biblioteca de juegos cuesta unos $45–100 al mes antes de haber jugado un solo minuto. El Cloud Sync propio de Vast no funciona en instancias KVM, y para jugar hace falta KVM: un contenedor no puede ejecutar su propio servidor gráfico en la GPU.

Esta configuración guarda en Google Drive solo lo que no se puede volver a descargar: partidas guardadas, prefijos de Proton, la sesión de Steam y la identidad de red. Normalmente son unos pocos gigabytes, a menudo dentro de los 15 GB gratuitos de Google. Cuando terminas, destruyes la instancia y pagas solo las horas que juegas: en una RTX 3060 a unos $0.11 la hora, cuatro horas diarias salen por unos $13.60 al mes con todo incluido; un par de tardes por semana, unos $2–3.

A cambio, cada instancia nueva vuelve a descargar tus juegos de Steam, normalmente en 10–20 minutos.

---

## Inicio rápido

Unos treinta minutos la primera vez. Necesitarás:

| | |
|---|---|
| una cuenta de **Vast.ai** | con algo de saldo |
| una cuenta de **Tailscale** | el plan gratuito basta |
| una cuenta de **Google** para Drive | una aparte, no la principal — ver [Seguridad](#seguridad) |
| **rclone** en tu equipo | una vez, para obtener el token de Drive |
| **Tailscale** y **Moonlight** en cada dispositivo desde el que vayas a jugar | teléfono, portátil, TV box |

### 1. Tailscale

1. En [login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys) pulsa **Generate auth key**:
   - **Reusable — activado.** La clave se usa en cada arranque.
   - **Ephemeral — desactivado.** Los nodos efímeros se eliminan en cuanto se desconectan, y perderías el nombre fijo.
   - Expiration — hasta 90 días. Cuando caduque, genera una nueva y actualiza la variable en Vast.

   La cadena `tskey-auth-...` es tu `TAILSCALE_AUTHKEY`.
2. En **DNS**, activa **MagicDNS**. En esa misma página aparece el nombre de tu tailnet, algo como `tail3d42c9.ts.net`. Lo necesitarás para Moonlight.
3. Instala Tailscale en cada dispositivo desde el que vayas a jugar e inicia sesión con la misma cuenta. Moonlight solo llega a la instancia a través de Tailscale.

### 2. Token de Google Drive

Instala rclone ([rclone.org/install](https://rclone.org/install/); en macOS `brew install rclone`, en Windows `winget install Rclone.Rclone`) y ejecuta:

```bash
rclone config
```

- `n` para un remoto nuevo, llámalo **gdrive**
- tipo de almacenamiento: **drive**
- `client_id` y `client_secret`: pulsa Enter en ambos
- scope: **1** (Full access)
- pulsa Enter en el resto; responde **y** a `Use auto config?` — se abrirá el navegador; inicia sesión con la cuenta de Google que usarás para esto y concede el acceso

Después:

```bash
rclone config show gdrive
```

En la línea `token = {...}` busca `"refresh_token":"1//0...."`. Copia lo que hay dentro de las comillas, sin las comillas: ese es tu `RCLONE_REFRESH_TOKEN`. Es largo y empieza por `1//`.

No hace falta crear nada en Drive: en el primer arranque el script crea una carpeta `vastai-cloud-games` en la raíz de Mi unidad y trabaja solo dentro de ella.

Dejar `client_id` vacío usa el cliente propio de rclone. Es más lento que uno tuyo, pero de sobra para esta configuración, y sus tokens no caducan. Lee [Tu propio cliente OAuth de Google](#tu-propio-cliente-oauth-de-google) antes de cambiarlo.

### 3. Guarda ambas claves en Vast

En Vast, abre **Settings** y baja hasta **Environment Variables**. Añade:

| Clave | Valor |
|---|---|
| `RCLONE_REFRESH_TOKEN` | el token del paso 2 |
| `TAILSCALE_AUTHKEY` | la clave del paso 1 |

Pulsa **+** después de cada una y, al final, **Save Edits**. Estas variables son de toda la cuenta y llegan a cada instancia que alquiles. Nunca las pongas en una plantilla.

### 4. Alquila con la plantilla

**[Abrir la plantilla en Vast.ai](https://cloud.vast.ai?ref_id=688160&template_id=49faada2e6e7fe3e15bef0ea1999420a)**

Al elegir máquina, fíjate en:

- **una tarjeta gráfica NVIDIA para juegos** — RTX o GTX, que tienen el codificador NVENC. Las GPU de centro de datos como la A100 o la H100 no tienen salida de vídeo y no funcionarán.
- **que esté cerca de ti** — la latencia depende de la distancia.
- **disco suficiente** para los juegos que vayas a instalar, más unos 20 GB.
- **que no cobre el tráfico.** Pasa el cursor sobre el precio: el desglose solo debe mostrar GPU y disco. La mayoría de los hosts no cobran el tráfico, pero lo decide cada uno, y un stream consume unos 10–20 GB por hora.

Luego pulsa **Rent**. La instalación tarda unos minutos.

Para los curiosos, esto es todo lo que la plantilla ejecuta al arrancar. Descarga el script y lo ejecuta:

```bash
#!/bin/bash
URL='https://gist.githubusercontent.com/WetFoxx/792f2333664d95c442fa451063b16436/raw/787acea52dc0d909adbf76bdf847b15a28ace4a9/gistfile1.txt'
for _ in {1..30}; do
  curl -fsSL "$URL" -o /root/setup.sh && [ -s /root/setup.sh ] && break
  sleep 5
done
exec bash /root/setup.sh
```

La URL apunta a una revisión fija del script, así que lo que se ejecuta no puede cambiar a tus espaldas.

### 5. Conecta Moonlight

Cuando la instancia esté lista, aparecerá un nodo `vastai-gaming` en [Machines](https://login.tailscale.com/admin/machines), en la consola de Tailscale. Ya que estás ahí, abre **⋯ → Disable key expiry** en ese nodo; si no, se desconectará dentro de seis meses.

El nombre completo del nodo es su nombre más el de tu tailnet:

```
vastai-gaming.tail3d42c9.ts.net
```

Si aparece como `vastai-gaming-2` o algo parecido, ese número viene de nodos anteriores con el mismo nombre. Elimina los obsoletos y usa el nombre exacto que veas.

En Moonlight, añade el PC **a mano, por ese nombre, no por dirección IP.** Cuando pida un PIN, abre `https://vastai-gaming.tail3d42c9.ts.net:47990` en el navegador. El aviso del certificado es normal. La primera vez, Sunshine te pedirá crear un usuario y una contraseña; después introduce el PIN en la sección PIN.

El emparejamiento se guarda en la nube, así que esto se hace una sola vez.

Pon la resolución de Moonlight en **Native** para obtener la resolución de tu pantalla: el escritorio se adapta a lo que pida el cliente.

### 6. Configura Steam una vez

1. Inicia sesión en Steam.
2. Instala tus juegos.
3. Activa el **modo sin conexión** de Steam (en el menú Steam). Sin él, cada instancia nueva te pedirá un código de Steam Guard.
4. Juega 10–15 minutos para que todo llegue a la nube.

Con eso termina la configuración. A partir de ahora: alquilas con la plantilla, te conectas y juegas.

---

## Cada sesión

Alquila con la plantilla, espera unos minutos y conecta Moonlight. Tus juegos de Steam se vuelven a descargar cada vez, normalmente en 10–20 minutos; las partidas guardadas y la configuración ya están ahí.

**Cuando termines: sal del juego, espera la notificación del escritorio y luego destruye la instancia.** La notificación —titulada «Сохранения в облаке», porque los mensajes del script están en ruso— aparece solo cuando tus partidas guardadas ya han llegado a Drive, así que es tu confirmación.

Destrúyela, no la detengas: una instancia detenida sigue cobrando por su disco.

La configuración de Steam y la caché del modo sin conexión se sincronizan cada cinco minutos. Si cambiaste algo justo antes de irte, abre una terminal en la instancia y ejecuta primero `sudo wolf shutdown`: cierra Steam y sube todo al momento.

Otros comandos, desde una terminal en la instancia o por SSH:

```bash
sudo wolf shutdown          # cerrar Steam y subir todo ahora mismo
sudo wolf state             # subir el estado ahora mismo
sudo wolf games             # subir ahora mismo los juegos que no son de Steam
sudo wolf restore NOMBRE... # restaurar archivos (FORCE=1 para restaurar aunque estén al día)
sudo wolf display           # reaplicar la configuración de pantalla (reinicia X; no en plena partida)
sudo wolf firewall          # reaplicar el aislamiento de puertos
sudo wolf-res 2560 1440 60  # cambiar la resolución a mano; sin argumentos vuelve a la base
```

Registros y estado:

| | |
|---|---|
| `/var/log/wolf-setup.log` | instalación; los problemas del entorno van marcados con `!!!` |
| `/var/log/wolf.log` | sincronización, red, pantalla |
| `/var/log/wolf-res.log` | cambios de resolución |
| `http://vastai-gaming.<tailnet>.ts.net:8099` | página de estado, solo por Tailscale; `/json` da los mismos datos para scripts |

---

## Avanzado

### Tu propio cliente OAuth de Google

Solo merece la pena si activas `SYNC_STEAM_GAMES=1` o sincronizas juegos grandes que no son de Steam: el cliente compartido de rclone tiene límites de velocidad, y uno propio es más rápido con volúmenes grandes. Instrucciones: [rclone.org/drive/#making-your-own-client-id](https://rclone.org/drive/#making-your-own-client-id).

**Tienes que publicarlo.** Un cliente nuevo empieza en estado **Testing**, y Google les da a esos clientes tokens que caducan a los 7 días: la sincronización se detendría sin avisar una semana después de configurarla. En Google Cloud Console abre la pantalla de consentimiento de OAuth (en la consola nueva: Google Auth Platform → Audience) y cambia el estado de publicación a **In production** (En producción). Para uso personal no hace falta verificación; al iniciar sesión, Google mostrará una vez un aviso de aplicación no verificada: elige Configuración avanzada y continúa.

Después repite el paso 2 introduciendo tus `client_id` y `client_secret`, y añade `RCLONE_CLIENT_ID` y `RCLONE_CLIENT_SECRET` en las Environment Variables de Vast, junto al nuevo token.

### ZeroTier

Si quieres una segunda red, pon su Network ID de [my.zerotier.com](https://my.zerotier.com) en `ZT_NETWORK_ID`. Sin esa variable, ZeroTier ni se instala ni se inicia. Para el stream basta con Tailscale.

### Tu propia plantilla

El script ocupa más de los 16 KB que Vast permite en el campo on-start; por eso la plantilla solo contiene un cargador. Para una plantilla propia: imagen `docker.io/vastai/kvm:ubuntu_desktop_22.04`, el cargador del paso 4 en **On-start script** y un disco a la medida de tus juegos. Si alojas tu propia copia del script, haz que la URL apunte a una revisión fija —una etiqueta de versión o un commit—, no a una rama; si no, cualquier cambio llegará al instante a todas las instancias que arranquen.

### Variables

Todas se definen en las Environment Variables de Vast y sustituyen los valores por defecto del script.

**Secretos**

| | |
|---|---|
| `RCLONE_REFRESH_TOKEN` | token de Google Drive |
| `TAILSCALE_AUTHKEY` | la clave `tskey-auth-...` |
| `RCLONE_CLIENT_ID`, `RCLONE_CLIENT_SECRET` | tu propio cliente OAuth de Google |
| `ZT_NETWORK_ID` | red de ZeroTier; vacío = sin ZeroTier |

**Comportamiento**

| | Por defecto | |
|---|---|---|
| `STEAM_FREEZE` | `1` | no dejar que el cliente de Steam se actualice al arrancar |
| `SYNC_STEAM_GAMES` | `0` | sincronizar también los juegos de Steam |
| `AUTO_RES` | `1` | gestionar la resolución |
| `RES` | `1920x1200` | resolución base del escritorio |
| `STEAM_LANG` | vacío | forzar un idioma de Steam (`spanish`, `latam`, `english`, …); vacío = Steam usa su propia configuración |
| `TAILSCALE_SSH` | `1` | activar Tailscale SSH — ver [Seguridad](#seguridad) |
| `NOTIFY_SAVES` | `1` | avisar cuando se suben las partidas guardadas |
| `NOTIFY_SEC` | `8` | cuántos segundos se muestra la notificación |

**Ajuste fino**

| | Por defecto | |
|---|---|---|
| `R_REMOTE` | `gdrive:vastai-cloud-games` | carpeta en Drive |
| `TAILSCALE_HOSTNAME` | `vastai-gaming` | nombre del nodo |
| `TAILSCALE_EXTRA_ARGS` | — | parámetros extra para `tailscale up` |
| `GAMES_DIR` | `~/Downloads/Games` | juegos que no son de Steam |
| `STATE_SYNC_MIN` | `5` | intervalo de sincronización del estado, minutos |
| `GAMES_SYNC_MIN` | `15` | intervalo de sincronización de juegos, minutos |
| `PAR` | `4` | archivos en paralelo |
| `ZSTD_STATE` / `ZSTD_GAMES` | `3` / `1` | nivel de compresión |
| `WOLF_PORT` | `8099` | puerto de la página de estado |
| `DESKTOP_USER` | uid 1000 | usuario del escritorio |

---

## Qué se sincroniza y cuándo

| Archivo | Contenido | Se sube |
|---|---|---|
| `identity` | `machine-id`, estado de Tailscale, claves de ZeroTier, certificados de Sunshine | cada 5 min |
| `steam-state` | sesión de Steam, configuración, `userdata/` | cada 5 min |
| `steam-client` | el cliente de Steam sin cachés ni juegos | cada 5 min, cuando se estabiliza |
| `steam-cache` | `appcache` para el modo sin conexión | cuando no cambia en dos pasadas seguidas, o al apagar si Steam ya está cerrado |
| `pfx--<appid>` | el prefijo de Proton, donde están las partidas guardadas | cada 5 min, y justo al salir del juego |
| `game--<carpeta>` | un juego que no es de Steam, subcarpeta de `~/Downloads/Games` | cada 15 min, y al salir |
| `sgame--<carpeta>` | un juego de Steam, solo con `SYNC_STEAM_GAMES=1` | cada 15 min, y al salir |

Los juegos de Steam **no** se sincronizan por defecto: volver a descargarlos de Steam suele ser más rápido que desde Drive y no gasta tu cuota de Google (750 GB de subida al día). Activa `SYNC_STEAM_GAMES=1` solo para juegos que hayas modificado o que ya no estén en la tienda, y en ese caso configura [tu propio cliente OAuth](#tu-propio-cliente-oauth-de-google).

Pon los juegos que no son de Steam en subcarpetas de `~/Downloads/Games`; cada subcarpeta se convierte en su propio archivo.

---

## Seguridad

Lo que hace el script:

- Los puertos de Sunshine (47984–48010, incluida la interfaz web en el 47990) y la página de estado solo aceptan tráfico de la interfaz `tailscale0` y de loopback; el resto se descarta. La política de INPUT no se toca, así que SSH y la gestión propia de Vast siguen funcionando.
- El token de Drive solo está en `/root/.config/rclone/rclone.conf` (600, root); la clave de Tailscale está en `/etc/wolf/tskey` (600, root) y se pasa como `file:`, así que nunca aparece en la lista de procesos. Ambos se eliminan de `/etc/environment`, y Steam y los juegos arrancan con `env -i`, sin heredar ningún secreto.
- El archivo `identity` nunca se descomprime en `/`. Se descarga a un archivo temporal, se comprueba que no contenga rutas absolutas ni `..`, se extrae en un directorio intermedio, cada enlace simbólico se verifica con `realpath` y solo una lista fija de ficheros se copia a su sitio. Los archivos de usuario siempre se extraen como el usuario sin privilegios del escritorio, nunca como root.
- Las subidas a Drive son atómicas: el archivo se escribe como `NOMBRE.part` y sustituye al definitivo solo si `moveto` termina bien. Las versiones sobrescritas van a la papelera de Drive, así que se pueden recuperar.

Lo que depende de ti:

- **Usa una cuenta de Google aparte.** El token de Drive puede leerlo cualquier cosa que se ejecute en la instancia, y da acceso completo a Drive. Una cuenta aparte limita el daño a la carpeta de juegos.
- **La instancia se la alquilas a un desconocido.** El dueño de la máquina tiene root en el host, y allí estarán tu sesión de Steam y tu token de Drive. No es paranoia, es lo que implica alquilar: decide qué estás dispuesto a dejar ahí.
- **Tailscale SSH está activado por defecto.** Cualquier dispositivo de tu tailnet obtiene root en la instancia. Es cómodo en una tailnet personal; pon `TAILSCALE_SSH=0` si la compartes.
- **No compartas nunca la carpeta de Drive.** Los archivos contienen una sesión de Steam activa.

---

## Limitaciones

- **Una sola instancia a la vez.** Las instancias comparten los mismos archivos y la misma identidad de Tailscale. El script detecta el desajuste de versiones y deja de subir, pero no confíes en ello.
- **Juegos con antitrampas** (EAC, BattlEye): pueden negarse a funcionar en una máquina virtual o hacer que te baneen la cuenta. Bajo tu responsabilidad.
- **Solo NVIDIA.** En otras GPU la gestión de la resolución se desactiva sola; lo demás funciona.
- Probado en `docker.io/vastai/kvm:ubuntu_desktop_22.04` con el gestor de pantalla SDDM. Otras imágenes pueden necesitar ajustes.
- Los mensajes del registro, las notificaciones y la página de estado del script están en ruso.

---

## Solución de problemas

**Moonlight no encuentra el PC; la página de estado no abre.** El dispositivo que usas no está en Tailscale, o MagicDNS está desactivado. Comprueba que Tailscale esté en marcha en él y con la sesión iniciada en la misma cuenta.

**La sincronización dejó de funcionar una semana después de configurarla.** Estás usando tu propio cliente OAuth de Google en estado Testing: ver [Tu propio cliente OAuth de Google](#tu-propio-cliente-oauth-de-google). Publícalo y obtén un token