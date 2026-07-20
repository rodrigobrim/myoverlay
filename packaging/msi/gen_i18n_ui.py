"""Generate i18n_ui.js - the MSI wizard UI translation custom action.

The wizard dialogs contain no literal text: every visible string is a
[P_*] property. The generated ApplyUiLanguage() JScript CA fills all of
them for the language in OUTPUT_LANGUAGE (machine-detected before the
first dialog, re-applied when the user changes the combo).

Regenerate after editing translations:
    uv run python packaging/msi/gen_i18n_ui.py
The output is ASCII-only (\\uXXXX escapes), so codepages never matter.
"""

from __future__ import annotations

import json
from pathlib import Path

# Base languages fill the dict(zip(L, [...])) tables below. Simplified Chinese
# is added afterwards from ZH (see merge) to avoid widening every row.
L = ["en", "pt", "es", "ja", "ar", "fr", "it", "ru"]
LANGS = L + ["zh"]

# fmt: off
S: dict[str, dict[str, str]] = {
    # -------- common buttons --------
    "BACK":       dict(zip(L, ["< &Back", "< &Voltar", "< &Atrás", "< 戻る(&B)", "< السابق(&B)", "< &Précédent", "< &Indietro", "< &Назад"])),
    "NEXT":       dict(zip(L, ["&Next >", "&Avançar >", "&Siguiente >", "次へ(&N) >", "التالي(&N) >", "&Suivant >", "&Avanti >", "&Далее >"])),
    "CANCELB":    dict(zip(L, ["Cancel", "Cancelar", "Cancelar", "キャンセル", "إلغاء", "Annuler", "Annulla", "Отмена"])),
    "INSTALL":    dict(zip(L, ["&Install", "&Instalar", "&Instalar", "インストール(&I)", "تثبيت(&I)", "&Installer", "&Installa", "&Установить"])),
    "FINISH":     dict(zip(L, ["&Finish", "&Concluir", "&Finalizar", "完了(&F)", "إنهاء(&F)", "&Terminer", "&Fine", "&Готово"])),
    "YES":        dict(zip(L, ["&Yes", "&Sim", "&Sí", "はい(&Y)", "نعم(&Y)", "&Oui", "&Sì", "&Да"])),
    "NO":         dict(zip(L, ["&No", "&Não", "&No", "いいえ(&N)", "لا(&N)", "&Non", "&No", "&Нет"])),
    "REMOVEB":    dict(zip(L, ["&Remove", "&Remover", "&Quitar", "削除(&R)", "إزالة(&R)", "&Supprimer", "&Rimuovi", "&Удалить"])),
    "REPAIRB":    dict(zip(L, ["&Repair", "&Reparar", "&Reparar", "修復(&P)", "إصلاح(&P)", "&Réparer", "&Ripristina", "&Восстановить"])),
    "SKIPBTN":    dict(zip(L, ["S&kip this step", "&Pular esta etapa", "&Omitir este paso", "この手順をスキップ(&K)", "تخطي هذه الخطوة(&K)", "&Ignorer cette étape", "&Salta questo passaggio", "&Пропустить этот шаг"])),
    "SKIPANYWAY": dict(zip(L, ["&Skip anyway", "&Pular mesmo assim", "&Omitir igualmente", "スキップする(&S)", "تخطي على أي حال(&S)", "&Ignorer quand même", "&Salta comunque", "&Всё равно пропустить"])),

    # -------- welcome --------
    "WELCOME_TITLE": dict(zip(L, ["Welcome to the MyOverlay Setup Wizard", "Bem-vindo ao assistente de instalação do MyOverlay", "Bienvenido al asistente de instalación de MyOverlay", "MyOverlay セットアップ ウィザードへようこそ", "مرحبا بك في معالج إعداد MyOverlay", "Bienvenue dans l'assistant d'installation de MyOverlay", "Benvenuto nella procedura di installazione di MyOverlay", "Добро пожаловать в мастер установки MyOverlay"])),
    "WELCOME_DESC": dict(zip(L, [
        "The Setup Wizard will install MyOverlay - the definitive overlay tool for MyChron users - on your computer. Click Next to continue or Cancel to exit.",
        "O assistente instalará o MyOverlay - a ferramenta de overlay definitiva para usuários MyChron - neste computador. Clique em Avançar para continuar ou em Cancelar para sair.",
        "El asistente instalará MyOverlay - la herramienta de overlay definitiva para usuarios de MyChron - en este equipo. Haga clic en Siguiente para continuar o en Cancelar para salir.",
        "セットアップ ウィザードは MyOverlay(MyChron ユーザーのための決定版オーバーレイ ツール)をこのコンピューターにインストールします。続行するには[次へ]、終了するには[キャンセル]をクリックしてください。",
        "سيقوم معالج الإعداد بتثبيت MyOverlay - أداة التراكب المثالية لمستخدمي MyChron - على هذا الكمبيوتر. انقر فوق التالي للمتابعة أو إلغاء للخروج.",
        "L'assistant va installer MyOverlay - l'outil d'overlay de référence pour les utilisateurs MyChron - sur cet ordinateur. Cliquez sur Suivant pour continuer ou sur Annuler pour quitter.",
        "La procedura installerà MyOverlay - lo strumento di overlay definitivo per gli utenti MyChron - su questo computer. Fare clic su Avanti per continuare o su Annulla per uscire.",
        "Мастер установит MyOverlay - незаменимый инструмент оверлеев для пользователей MyChron - на этот компьютер. Нажмите «Далее», чтобы продолжить, или «Отмена», чтобы выйти.",
    ])),

    # -------- language page --------
    "LANG_TITLE": dict(zip(L, ["Video language", "Idioma dos vídeos", "Idioma de los vídeos", "動画の言語", "لغة الفيديو", "Langue des vidéos", "Lingua dei video", "Язык видео"])),
    "LANG_DESC":  dict(zip(L, ["Choose the language of the video output.", "Escolha o idioma do vídeo final.", "Elija el idioma del vídeo final.", "出力動画の言語を選択してください。", "اختر لغة الفيديو الناتج.", "Choisissez la langue des vidéos produites.", "Scegli la lingua dei video prodotti.", "Выберите язык итогового видео."])),
    "LANG_EXPLAIN": dict(zip(L, [
        "The selected language applies to the delta overlay labels and to the YouTube video title and description. The configuration files stay in English.",
        "O idioma selecionado vale para os rótulos do overlay (delta) e para o título e a descrição do vídeo no YouTube. Os arquivos de configuração permanecem em inglês.",
        "El idioma seleccionado se aplica a las etiquetas del overlay (delta) y al título y la descripción del vídeo en YouTube. Los archivos de configuración permanecen en inglés.",
        "選択した言語は、デルタ オーバーレイのラベルと YouTube 動画のタイトル/説明に適用されます。設定ファイルは英語のままです。",
        "تنطبق اللغة المختارة على تسميات طبقة الدلتا وعلى عنوان ووصف فيديو YouTube. تبقى ملفات الإعداد باللغة الإنجليزية.",
        "La langue choisie s'applique aux libellés de l'overlay (delta) ainsi qu'au titre et à la description de la vidéo YouTube. Les fichiers de configuration restent en anglais.",
        "La lingua scelta vale per le etichette dell'overlay (delta) e per il titolo e la descrizione del video su YouTube. I file di configurazione restano in inglese.",
        "Выбранный язык применяется к подписям оверлея (дельта) и к названию и описанию видео на YouTube. Файлы конфигурации остаются на английском.",
    ])),
    "LANG_LABEL": dict(zip(L, ["Language:", "Idioma:", "Idioma:", "言語:", "اللغة:", "Langue :", "Lingua:", "Язык:"])),

    # -------- gcloud page --------
    "GC_TITLE": dict(zip(L, ["Google Cloud SDK", "Google Cloud SDK", "Google Cloud SDK", "Google Cloud SDK", "Google Cloud SDK", "Google Cloud SDK", "Google Cloud SDK", "Google Cloud SDK"])),
    "GC_DESC":  dict(zip(L, ["The Google Cloud SDK was not found on this computer.", "O Google Cloud SDK não foi encontrado neste computador.", "No se encontró el Google Cloud SDK en este equipo.", "このコンピューターに Google Cloud SDK が見つかりませんでした。", "لم يتم العثور على Google Cloud SDK على هذا الكمبيوتر.", "Le Google Cloud SDK n'a pas été trouvé sur cet ordinateur.", "Google Cloud SDK non è stato trovato su questo computer.", "Google Cloud SDK не найден на этом компьютере."])),
    "GC_EXPLAIN": dict(zip(L, [
        "The Google Cloud SDK is included with MyOverlay and will be set up when you continue. It is required by the Google API configuration in the next steps.",
        "O Google Cloud SDK acompanha o MyOverlay e será configurado ao continuar. Ele é necessário para a configuração da API do Google nas próximas etapas.",
        "El Google Cloud SDK viene incluido con MyOverlay y se configurará al continuar. Es necesario para la configuración de la API de Google en los pasos siguientes.",
        "Google Cloud SDK は MyOverlay に含まれており、続行すると設定されます。次の手順の Google API 設定に必要です。",
        "يأتي Google Cloud SDK مع MyOverlay وسيتم إعداده عند المتابعة. وهو مطلوب لإعداد Google API في الخطوات التالية.",
        "Le Google Cloud SDK est fourni avec MyOverlay et sera configuré lorsque vous continuerez. Il est nécessaire à la configuration de l'API Google des étapes suivantes.",
        "Il Google Cloud SDK è incluso in MyOverlay e verrà configurato quando si continua. È necessario per la configurazione dell'API Google dei passaggi successivi.",
        "Google Cloud SDK входит в состав MyOverlay и будет настроен при продолжении. Он необходим для настройки Google API на следующих шагах.",
    ])),

    # -------- shortcuts page --------
    "SC_TITLE": dict(zip(L, ["Shortcuts", "Atalhos", "Accesos directos", "ショートカット", "الاختصارات", "Raccourcis", "Collegamenti", "Ярлыки"])),
    "SC_DESC":  dict(zip(L, ["Choose which shortcuts to create.", "Escolha quais atalhos criar.", "Elija qué accesos directos crear.", "作成するショートカットを選択してください。", "اختر الاختصارات التي تريد إنشاءها.", "Choisissez les raccourcis à créer.", "Scegli quali collegamenti creare.", "Выберите, какие ярлыки создать."])),
    "SC_START": dict(zip(L, ["Create a Start Menu shortcut", "Criar atalho no Menu Iniciar", "Crear acceso directo en el menú Inicio", "スタート メニューにショートカットを作成", "إنشاء اختصار في قائمة ابدأ", "Créer un raccourci dans le menu Démarrer", "Crea un collegamento nel menu Start", "Создать ярлык в меню «Пуск»"])),
    "SC_DESKTOP": dict(zip(L, ["Create a Desktop icon", "Criar ícone na Área de Trabalho", "Crear icono en el Escritorio", "デスクトップにアイコンを作成", "إنشاء أيقونة على سطح المكتب", "Créer une icône sur le Bureau", "Crea un'icona sul Desktop", "Создать значок на рабочем столе"])),
    "SC_NOTE": dict(zip(L, ["The shortcuts start MyOverlay's zero-touch workflow (myoverlay run).", "Os atalhos iniciam o fluxo automático do MyOverlay (myoverlay run).", "Los accesos directos inician el flujo automático de MyOverlay (myoverlay run).", "ショートカットは MyOverlay の自動ワークフロー (myoverlay run) を起動します。", "تشغل الاختصارات سير عمل MyOverlay التلقائي (myoverlay run).", "Les raccourcis lancent le flux automatique de MyOverlay (myoverlay run).", "I collegamenti avviano il flusso automatico di MyOverlay (myoverlay run).", "Ярлыки запускают автоматический рабочий процесс MyOverlay (myoverlay run)."])),

    # -------- google api page --------
    "GA_TITLE": dict(zip(L, ["Google API configuration (YouTube upload)", "Configuração da API do Google (envio ao YouTube)", "Configuración de la API de Google (subida a YouTube)", "Google API の設定 (YouTube アップロード)", "إعداد Google API (رفع إلى YouTube)", "Configuration de l'API Google (envoi YouTube)", "Configurazione dell'API Google (caricamento su YouTube)", "Настройка Google API (загрузка на YouTube)"])),
    "GA_DESC":  dict(zip(L, ["One-time setup so finished videos can be uploaded to YouTube (unlisted by default).", "Configuração única para enviar os vídeos prontos ao YouTube (não listados por padrão).", "Configuración única para subir los vídeos terminados a YouTube (ocultos por defecto).", "完成した動画を YouTube にアップロードするための 1 回限りの設定です (既定では限定公開)。", "إعداد لمرة واحدة لرفع الفيديوهات الجاهزة إلى YouTube (غير مدرجة افتراضيا).", "Configuration unique pour envoyer les vidéos terminées sur YouTube (non répertoriées par défaut).", "Configurazione una tantum per caricare i video finiti su YouTube (non in elenco per impostazione predefinita).", "Одноразовая настройка для загрузки готовых видео на YouTube (по умолчанию - доступ по ссылке)."])),
    "GA_WHAT_H": dict(zip(L, ["What setup will do", "O que a instalação fará", "Qué hará la instalación", "セットアップが行うこと", "ما الذي سيقوم به الإعداد", "Ce que l'installation fera", "Cosa farà l'installazione", "Что сделает установка"])),
    "GA_WHAT": dict(zip(L, [
        "MyOverlay sets up Google for you: it prepares your account for YouTube uploads and saves the access it needs, so you never open the Google Cloud Console yourself.",
        "O MyOverlay cuida da configuração do Google para você: prepara sua conta para envios ao YouTube e salva o acesso necessário, sem que você precise abrir o Google Cloud Console.",
        "MyOverlay se encarga de la configuración de Google por usted: prepara su cuenta para las subidas a YouTube y guarda el acceso necesario, sin que usted abra la Cloud Console de Google.",
        "MyOverlay が Google の設定を代行します。YouTube アップロード用にアカウントを準備し、必要なアクセス権を保存するため、Google Cloud Console を自分で操作する必要はありません。",
        "يتولى MyOverlay إعداد Google نيابة عنك: يهيئ حسابك للرفع إلى YouTube ويحفظ الوصول اللازم، دون أن تفتح Google Cloud Console بنفسك.",
        "MyOverlay s'occupe de la configuration Google pour vous : il prépare votre compte pour les envois YouTube et enregistre l'accès nécessaire, sans que vous ouvriez la Cloud Console Google.",
        "MyOverlay si occupa della configurazione di Google per te: prepara il tuo account per i caricamenti su YouTube e salva l'accesso necessario, senza che tu apra la Google Cloud Console.",
        "MyOverlay настраивает Google за вас: подготавливает аккаунт для загрузок на YouTube и сохраняет нужный доступ, так что вам не придётся открывать Google Cloud Console.",
    ])),
    "GA_WHY_H": dict(zip(L, ["Why it is needed", "Por que é necessário", "Por qué es necesario", "必要な理由", "لماذا هو مطلوب", "Pourquoi c'est nécessaire", "Perché serve", "Зачем это нужно"])),
    "GA_WHY": dict(zip(L, [
        "It is required only to upload rendered videos to YouTube. Everything else - camera/telemetry ingest, MyChron sync and overlay render - works without it.",
        "É necessário apenas para enviar os vídeos renderizados ao YouTube. Todo o resto - importação de câmera/telemetria, sincronização do MyChron e renderização do overlay - funciona sem ele.",
        "Solo es necesario para subir los vídeos renderizados a YouTube. Todo lo demás - importación de cámara/telemetría, sincronización de MyChron y renderizado del overlay - funciona sin él.",
        "レンダリング済み動画を YouTube にアップロードする場合にのみ必要です。それ以外 (カメラ/テレメトリ取り込み、MyChron 同期、オーバーレイ レンダリング) はなくても動作します。",
        "مطلوب فقط لرفع الفيديوهات المعالجة إلى YouTube. كل شيء آخر - استيراد الكاميرا/التليمترية ومزامنة MyChron ومعالجة الطبقة - يعمل بدونه.",
        "Il n'est requis que pour envoyer les vidéos rendues sur YouTube. Tout le reste - import caméra/télémétrie, synchronisation MyChron et rendu de l'overlay - fonctionne sans.",
        "Serve solo per caricare i video renderizzati su YouTube. Tutto il resto - import di camera/telemetria, sincronizzazione MyChron e rendering dell'overlay - funziona senza.",
        "Требуется только для загрузки готовых видео на YouTube. Всё остальное - импорт с камеры/телеметрии, синхронизация MyChron и рендеринг оверлея - работает без него.",
    ])),
    "GA_NEXT_H": dict(zip(L, ["What you will do", "O que você fará", "Qué hará usted", "あなたが行うこと", "ما الذي ستفعله", "Ce que vous ferez", "Cosa farai tu", "Что нужно сделать вам"])),
    "GA_NEXT": dict(zip(L, [
        "The first time you run MyOverlay, a normal browser window opens once. Sign in with the Google account that owns your YouTube channel - the window then closes by itself and setup finishes automatically.",
        "Na primeira execução do MyOverlay, uma janela normal do navegador abre uma única vez. Entre com a conta Google dona do seu canal do YouTube - a janela se fecha sozinha e a configuração termina automaticamente.",
        "La primera vez que ejecute MyOverlay, se abrirá una ventana normal del navegador una sola vez. Inicie sesión con la cuenta de Google propietaria de su canal de YouTube - la ventana se cierra sola y la configuración termina automáticamente.",
        "MyOverlay の初回実行時に、通常のブラウザー ウィンドウが 1 回だけ開きます。YouTube チャンネルを所有する Google アカウントでサインインしてください。ウィンドウは自動的に閉じ、設定は自動的に完了します。",
        "عند تشغيل MyOverlay لأول مرة، تفتح نافذة متصفح عادية مرة واحدة. سجل الدخول بحساب Google المالك لقناتك على YouTube - ثم تغلق النافذة تلقائيا ويكتمل الإعداد.",
        "Au premier lancement de MyOverlay, une fenêtre de navigateur s'ouvre une seule fois. Connectez-vous avec le compte Google propriétaire de votre chaîne YouTube - la fenêtre se ferme ensuite d'elle-même et la configuration se termine automatiquement.",
        "Al primo avvio di MyOverlay si apre una normale finestra del browser, una sola volta. Accedi con l'account Google proprietario del tuo canale YouTube - la finestra si chiude da sola e la configurazione termina automaticamente.",
        "При первом запуске MyOverlay один раз откроется обычное окно браузера. Войдите в аккаунт Google, которому принадлежит ваш канал YouTube, - окно закроется само, и настройка завершится автоматически.",
    ])),
    "GA_SKIPNOTE": dict(zip(L, ["Skip if you do not want YouTube publishing.", "Pule se não quiser publicar no YouTube.", "Omítalo si no quiere publicar en YouTube.", "YouTube への公開が不要ならスキップしてください。", "تخط إذا كنت لا تريد النشر على YouTube.", "Ignorez si vous ne voulez pas publier sur YouTube.", "Salta se non vuoi pubblicare su YouTube.", "Пропустите, если публикация на YouTube не нужна."])),

    # -------- skip warning page --------
    "SW_TITLE": dict(zip(L, ["Warning: YouTube publishing will be disabled", "Aviso: a publicação no YouTube ficará desativada", "Aviso: la publicación en YouTube quedará desactivada", "警告: YouTube への公開が無効になります", "تحذير: سيتم تعطيل النشر على YouTube", "Attention : la publication YouTube sera désactivée", "Avviso: la pubblicazione su YouTube sarà disattivata", "Внимание: публикация на YouTube будет отключена"])),
    "SW_DESC": dict(zip(L, ["You chose to skip the Google API configuration.", "Você optou por pular a configuração da API do Google.", "Ha decidido omitir la configuración de la API de Google.", "Google API の設定をスキップすることを選びました。", "اخترت تخطي إعداد Google API.", "Vous avez choisi d'ignorer la configuration de l'API Google.", "Hai scelto di saltare la configurazione dell'API Google.", "Вы решили пропустить настройку Google API."])),
    "SW_TEXT": dict(zip(L, [
        "Without the Google API configuration, MyOverlay will NOT upload rendered videos to YouTube (uploads are unlisted by default). Everything else - camera/telemetry ingest, MyChron sync and overlay render - works normally.\n\nYou can configure it later at any time: run 'myoverlay google-setup' (it opens the same one-time browser sign-in), or follow the 'YouTube setup' section of the README.",
        "Sem a configuração da API do Google, o MyOverlay NÃO enviará os vídeos renderizados ao YouTube (envios são não listados por padrão). Todo o resto - importação de câmera/telemetria, sincronização do MyChron e renderização do overlay - funciona normalmente.\n\nVocê pode configurar depois, a qualquer momento: execute 'myoverlay google-setup' (abre o mesmo login único no navegador) ou siga a seção 'YouTube setup' do README.",
        "Sin la configuración de la API de Google, MyOverlay NO subirá los vídeos renderizados a YouTube (las subidas son ocultas por defecto). Todo lo demás - importación de cámara/telemetría, sincronización de MyChron y renderizado del overlay - funciona con normalidad.\n\nPuede configurarlo más tarde en cualquier momento: ejecute 'myoverlay google-setup' (abre el mismo inicio de sesión único en el navegador) o siga la sección 'YouTube setup' del README.",
        "Google API を設定しない場合、MyOverlay はレンダリング済み動画を YouTube にアップロードしません (アップロードは既定で限定公開)。それ以外 (カメラ/テレメトリ取り込み、MyChron 同期、オーバーレイ レンダリング) は通常どおり動作します。\n\n後からいつでも設定できます: 'myoverlay google-setup' を実行するか (同じ 1 回限りのブラウザー サインインが開きます)、README の 'YouTube setup' の手順に従ってください。",
        "بدون إعداد Google API لن يقوم MyOverlay برفع الفيديوهات المعالجة إلى YouTube (الرفع غير مدرج افتراضيا). كل شيء آخر - استيراد الكاميرا/التليمترية ومزامنة MyChron ومعالجة الطبقة - يعمل بشكل طبيعي.\n\nيمكنك الإعداد لاحقا في أي وقت: شغل 'myoverlay google-setup' (يفتح نفس تسجيل الدخول لمرة واحدة في المتصفح)، أو اتبع قسم 'YouTube setup' في README.",
        "Sans la configuration de l'API Google, MyOverlay n'enverra PAS les vidéos rendues sur YouTube (les envois sont non répertoriés par défaut). Tout le reste - import caméra/télémétrie, synchronisation MyChron et rendu de l'overlay - fonctionne normalement.\n\nVous pourrez la configurer plus tard à tout moment : exécutez 'myoverlay google-setup' (même connexion unique dans le navigateur) ou suivez la section 'YouTube setup' du README.",
        "Senza la configurazione dell'API Google, MyOverlay NON caricherà i video renderizzati su YouTube (i caricamenti sono non in elenco per impostazione predefinita). Tutto il resto - import camera/telemetria, sincronizzazione MyChron e rendering dell'overlay - funziona normalmente.\n\nPotrai configurarla in qualsiasi momento: esegui 'myoverlay google-setup' (apre lo stesso accesso una tantum nel browser) oppure segui la sezione 'YouTube setup' del README.",
        "Без настройки Google API MyOverlay НЕ будет загружать готовые видео на YouTube (загрузки по умолчанию с доступом по ссылке). Всё остальное - импорт с камеры/телеметрии, синхронизация MyChron и рендеринг оверлея - работает как обычно.\n\nНастроить можно позже в любой момент: выполните 'myoverlay google-setup' (откроется тот же одноразовый вход в браузере) или следуйте разделу 'YouTube setup' в README.",
    ])),

    # -------- resolution page --------
    "RES_TITLE": dict(zip(L, ["Default output resolution", "Resolução padrão de saída", "Resolución de salida predeterminada", "既定の出力解像度", "دقة الإخراج الافتراضية", "Résolution de sortie par défaut", "Risoluzione di output predefinita", "Разрешение вывода по умолчанию"])),
    "RES_DESC": dict(zip(L, ["Choose the default resolution of rendered videos.", "Escolha a resolução padrão dos vídeos renderizados.", "Elija la resolución predeterminada de los vídeos renderizados.", "レンダリングする動画の既定の解像度を選択してください。", "اختر الدقة الافتراضية للفيديوهات المعالجة.", "Choisissez la résolution par défaut des vidéos rendues.", "Scegli la risoluzione predefinita dei video renderizzati.", "Выберите разрешение готовых видео по умолчанию."])),
    "RES_EXPLAIN": dict(zip(L, [
        "Footage is scaled to this height when rendering the overlay. It can be changed later in config.toml or per render with --resolution.",
        "As imagens são redimensionadas para esta altura ao renderizar o overlay. Pode ser alterada depois no config.toml ou por renderização com --resolution.",
        "Las imágenes se escalan a esta altura al renderizar el overlay. Se puede cambiar más tarde en config.toml o por renderizado con --resolution.",
        "オーバーレイのレンダリング時に、映像はこの高さにスケーリングされます。後から config.toml で、またはレンダリングごとに --resolution で変更できます。",
        "يتم تغيير حجم اللقطات إلى هذا الارتفاع عند معالجة الطبقة. يمكن تغييرها لاحقا في config.toml أو لكل معالجة عبر ‎--resolution.",
        "Les images sont mises à l'échelle à cette hauteur lors du rendu de l'overlay. Modifiable ensuite dans config.toml ou par rendu avec --resolution.",
        "Le riprese vengono scalate a questa altezza durante il rendering dell'overlay. Può essere cambiata in seguito in config.toml o per singolo rendering con --resolution.",
        "Кадры масштабируются до этой высоты при рендеринге оверлея. Позже можно изменить в config.toml или для отдельного рендеринга через --resolution.",
    ])),
    "RES_LABEL": dict(zip(L, ["Resolution:", "Resolução:", "Resolución:", "解像度:", "الدقة:", "Résolution :", "Risoluzione:", "Разрешение:"])),

    # -------- verify ready / progress / exit / cancel --------
    "VR_TITLE": dict(zip(L, ["Ready to install", "Pronto para instalar", "Listo para instalar", "インストールの準備完了", "جاهز للتثبيت", "Prêt à installer", "Pronto per l'installazione", "Всё готово к установке"])),
    "VR_DESC": dict(zip(L, [
        "Click Install to begin. Click Back to review or change your choices, or Cancel to exit.",
        "Clique em Instalar para começar. Clique em Voltar para revisar ou alterar as opções, ou em Cancelar para sair.",
        "Haga clic en Instalar para comenzar. Haga clic en Atrás para revisar o cambiar sus opciones, o en Cancelar para salir.",
        "[インストール]をクリックすると開始します。[戻る]で選択内容を確認/変更、[キャンセル]で終了します。",
        "انقر فوق تثبيت للبدء. انقر فوق السابق لمراجعة اختياراتك أو تغييرها، أو إلغاء للخروج.",
        "Cliquez sur Installer pour commencer. Cliquez sur Précédent pour revoir ou modifier vos choix, ou sur Annuler pour quitter.",
        "Fai clic su Installa per iniziare. Fai clic su Indietro per rivedere o modificare le scelte, o su Annulla per uscire.",
        "Нажмите «Установить», чтобы начать. Нажмите «Назад», чтобы просмотреть или изменить выбор, или «Отмена», чтобы выйти.",
    ])),
    "VRM_TITLE": dict(zip(L, ["Ready to remove", "Pronto para remover", "Listo para quitar", "削除の準備完了", "جاهز للإزالة", "Prêt à supprimer", "Pronto per la rimozione", "Всё готово к удалению"])),
    "VRM_DESC": dict(zip(L, [
        "Click Remove to remove MyOverlay from your computer. Click Back to review or change your choices, or Cancel to exit.",
        "Clique em Remover para remover o MyOverlay do computador. Clique em Voltar para revisar ou alterar as opções, ou em Cancelar para sair.",
        "Haga clic en Quitar para eliminar MyOverlay del equipo. Haga clic en Atrás para revisar o cambiar sus opciones, o en Cancelar para salir.",
        "[削除]をクリックすると MyOverlay をコンピューターから削除します。[戻る]で選択内容を確認/変更、[キャンセル]で終了します。",
        "انقر فوق إزالة لإزالة MyOverlay من الكمبيوتر. انقر فوق السابق لمراجعة اختياراتك أو تغييرها، أو إلغاء للخروج.",
        "Cliquez sur Supprimer pour retirer MyOverlay de l'ordinateur. Cliquez sur Précédent pour revoir ou modifier vos choix, ou sur Annuler pour quitter.",
        "Fai clic su Rimuovi per rimuovere MyOverlay dal computer. Fai clic su Indietro per rivedere o modificare le scelte, o su Annulla per uscire.",
        "Нажмите «Удалить», чтобы удалить MyOverlay с компьютера. Нажмите «Назад», чтобы просмотреть или изменить выбор, или «Отмена», чтобы выйти.",
    ])),
    "VRP_TITLE": dict(zip(L, ["Ready to repair", "Pronto para reparar", "Listo para reparar", "修復の準備完了", "جاهز للإصلاح", "Prêt à réparer", "Pronto per il ripristino", "Всё готово к восстановлению"])),
    "VRP_DESC": dict(zip(L, [
        "Click Repair to repair the installation. Click Back to go back, or Cancel to exit.",
        "Clique em Reparar para reparar a instalação. Clique em Voltar para retornar ou em Cancelar para sair.",
        "Haga clic en Reparar para reparar la instalación. Haga clic en Atrás para volver o en Cancelar para salir.",
        "[修復]をクリックするとインストールを修復します。[戻る]で戻る、[キャンセル]で終了します。",
        "انقر فوق إصلاح لإصلاح التثبيت. انقر فوق السابق للرجوع، أو إلغاء للخروج.",
        "Cliquez sur Réparer pour réparer l'installation. Cliquez sur Précédent pour revenir, ou sur Annuler pour quitter.",
        "Fai clic su Ripristina per riparare l'installazione. Fai clic su Indietro per tornare, o su Annulla per uscire.",
        "Нажмите «Восстановить», чтобы восстановить установку. Нажмите «Назад», чтобы вернуться, или «Отмена», чтобы выйти.",
    ])),
    "PROG_TITLE": dict(zip(L, ["Installing MyOverlay", "Instalando o MyOverlay", "Instalando MyOverlay", "MyOverlay をインストールしています", "جار تثبيت MyOverlay", "Installation de MyOverlay", "Installazione di MyOverlay", "Установка MyOverlay"])),
    "PROG_WAIT": dict(zip(L, [
        "Please wait while the Setup Wizard performs the requested operation. This may take several minutes.",
        "Aguarde enquanto o assistente executa a operação solicitada. Isso pode levar alguns minutos.",
        "Espere mientras el asistente realiza la operación solicitada. Esto puede tardar varios minutos.",
        "セットアップ ウィザードが要求された操作を実行しています。しばらくお待ちください。数分かかることがあります。",
        "يرجى الانتظار بينما ينفذ معالج الإعداد العملية المطلوبة. قد يستغرق ذلك عدة دقائق.",
        "Veuillez patienter pendant que l'assistant effectue l'opération demandée. Cela peut prendre plusieurs minutes.",
        "Attendere mentre la procedura esegue l'operazione richiesta. L'operazione può richiedere alcuni minuti.",
        "Подождите, пока мастер выполняет запрошенную операцию. Это может занять несколько минут.",
    ])),
    "PROG_STATUS": dict(zip(L, ["Status:", "Status:", "Estado:", "状態:", "الحالة:", "Statut :", "Stato:", "Состояние:"])),
    "EXIT_TITLE": dict(zip(L, ["Completed the MyOverlay Setup Wizard", "Assistente do MyOverlay concluído", "Asistente de MyOverlay completado", "MyOverlay セットアップ ウィザードが完了しました", "اكتمل معالج إعداد MyOverlay", "Assistant MyOverlay terminé", "Procedura di installazione di MyOverlay completata", "Мастер установки MyOverlay завершён"])),
    "EXIT_DESC": dict(zip(L, ["Click Finish to exit the Setup Wizard.", "Clique em Concluir para sair do assistente.", "Haga clic en Finalizar para salir del asistente.", "[完了]をクリックしてウィザードを終了してください。", "انقر فوق إنهاء للخروج من المعالج.", "Cliquez sur Terminer pour quitter l'assistant.", "Fai clic su Fine per uscire dalla procedura.", "Нажмите «Готово», чтобы выйти из мастера."])),
    "CANCEL_MSG": dict(zip(L, ["Are you sure you want to cancel MyOverlay installation?", "Tem certeza de que deseja cancelar a instalação do MyOverlay?", "¿Seguro que desea cancelar la instalación de MyOverlay?", "MyOverlay のインストールをキャンセルしますか?", "هل تريد بالتأكيد إلغاء تثبيت MyOverlay؟", "Voulez-vous vraiment annuler l'installation de MyOverlay ?", "Annullare davvero l'installazione di MyOverlay?", "Вы действительно хотите отменить установку MyOverlay?"])),

    # -------- maintenance --------
    "MW_TITLE": dict(zip(L, ["MyOverlay maintenance", "Manutenção do MyOverlay", "Mantenimiento de MyOverlay", "MyOverlay のメンテナンス", "صيانة MyOverlay", "Maintenance de MyOverlay", "Manutenzione di MyOverlay", "Обслуживание MyOverlay"])),
    "MW_DESC": dict(zip(L, [
        "The Setup Wizard lets you repair or remove MyOverlay. Click Next to continue or Cancel to exit.",
        "O assistente permite reparar ou remover o MyOverlay. Clique em Avançar para continuar ou em Cancelar para sair.",
        "El asistente permite reparar o quitar MyOverlay. Haga clic en Siguiente para continuar o en Cancelar para salir.",
        "このウィザードでは MyOverlay の修復または削除ができます。続行するには[次へ]、終了するには[キャンセル]をクリックしてください。",
        "يتيح لك المعالج إصلاح MyOverlay أو إزالته. انقر فوق التالي للمتابعة أو إلغاء للخروج.",
        "L'assistant permet de réparer ou de supprimer MyOverlay. Cliquez sur Suivant pour continuer ou sur Annuler pour quitter.",
        "La procedura consente di riparare o rimuovere MyOverlay. Fai clic su Avanti per continuare o su Annulla per uscire.",
        "Мастер позволяет восстановить или удалить MyOverlay. Нажмите «Далее», чтобы продолжить, или «Отмена», чтобы выйти.",
    ])),
    "MT_TITLE": dict(zip(L, ["Repair or remove", "Reparar ou remover", "Reparar o quitar", "修復または削除", "إصلاح أو إزالة", "Réparer ou supprimer", "Ripristina o rimuovi", "Восстановить или удалить"])),
    "MT_DESC": dict(zip(L, ["Choose the operation to perform.", "Escolha a operação a executar.", "Elija la operación a realizar.", "実行する操作を選択してください。", "اختر العملية المطلوبة.", "Choisissez l'opération à effectuer.", "Scegli l'operazione da eseguire.", "Выберите операцию."])),
    "MT_REPAIR_DESC": dict(zip(L, ["Repairs the installation (missing or corrupt files, shortcuts and registry entries).", "Repara a instalação (arquivos, atalhos e entradas de registro ausentes ou corrompidos).", "Repara la instalación (archivos, accesos directos y entradas de registro ausentes o dañados).", "インストールを修復します (欠落/破損したファイル、ショートカット、レジストリ エントリ)。", "يصلح التثبيت (الملفات أو الاختصارات أو إدخالات السجل المفقودة أو التالفة).", "Répare l'installation (fichiers, raccourcis et entrées de registre manquants ou corrompus).", "Ripara l'installazione (file, collegamenti e voci di registro mancanti o danneggiati).", "Восстанавливает установку (отсутствующие или повреждённые файлы, ярлыки и записи реестра)."])),
    "MT_REMOVE_DESC": dict(zip(L, ["Removes MyOverlay from this computer.", "Remove o MyOverlay deste computador.", "Quita MyOverlay de este equipo.", "このコンピューターから MyOverlay を削除します。", "يزيل MyOverlay من هذا الكمبيوتر.", "Supprime MyOverlay de cet ordinateur.", "Rimuove MyOverlay da questo computer.", "Удаляет MyOverlay с этого компьютера."])),

    # -------- remove options --------
    "RO_TITLE": dict(zip(L, ["Uninstall options", "Opções de desinstalação", "Opciones de desinstalación", "アンインストール オプション", "خيارات إزالة التثبيت", "Options de désinstallation", "Opzioni di disinstallazione", "Параметры удаления"])),
    "RO_DESC": dict(zip(L, ["Choose what to remove.", "Escolha o que remover.", "Elija qué quitar.", "削除する項目を選択してください。", "اختر ما تريد إزالته.", "Choisissez ce qu'il faut supprimer.", "Scegli cosa rimuovere.", "Выберите, что удалить."])),
    "RO_EXPLAIN": dict(zip(L, [
        "Uninstalling removes everything the software installed: the application, its shortcuts, and its working data (the app's working copy, config.toml and Google credentials under %LOCALAPPDATA%\\myoverlay).",
        "A desinstalação remove tudo o que o software instalou: o aplicativo, seus atalhos e seus dados de trabalho (a cópia de trabalho do app, o config.toml e as credenciais do Google em %LOCALAPPDATA%\\myoverlay).",
        "La desinstalación elimina todo lo que instaló el software: la aplicación, sus accesos directos y sus datos de trabajo (la copia de trabajo de la app, config.toml y las credenciales de Google en %LOCALAPPDATA%\\myoverlay).",
        "アンインストールすると、ソフトウェアがインストールしたものをすべて削除します: アプリケーション、ショートカット、作業データ (%LOCALAPPDATA%\\myoverlay 内のアプリの作業コピー、config.toml、Google 資格情報)。",
        "تزيل إزالة التثبيت كل ما ثبته البرنامج: التطبيق واختصاراته وبيانات عمله (نسخة عمل التطبيق و config.toml وبيانات اعتماد Google في ‎%LOCALAPPDATA%\\myoverlay).",
        "La désinstallation supprime tout ce que le logiciel a installé : l'application, ses raccourcis et ses données de travail (la copie de travail de l'app, config.toml et les identifiants Google sous %LOCALAPPDATA%\\myoverlay).",
        "La disinstallazione rimuove tutto ciò che il software ha installato: l'applicazione, i suoi collegamenti e i suoi dati di lavoro (la copia di lavoro dell'app, config.toml e le credenziali Google in %LOCALAPPDATA%\\myoverlay).",
        "Удаление убирает всё, что установила программа: приложение, его ярлыки и рабочие данные (рабочую копию приложения, config.toml и учётные данные Google в %LOCALAPPDATA%\\myoverlay).",
    ])),
    "RO_GCLOUD": dict(zip(L, ["Also uninstall the Google Cloud SDK", "Desinstalar também o Google Cloud SDK", "Desinstalar también el Google Cloud SDK", "Google Cloud SDK もアンインストールする", "إزالة Google Cloud SDK أيضا", "Désinstaller aussi le Google Cloud SDK", "Disinstalla anche il Google Cloud SDK", "Также удалить Google Cloud SDK"])),
    "RO_GCLOUD_NOTE": dict(zip(L, ["Leave unchecked if other tools on this computer use it.", "Deixe desmarcado se outras ferramentas deste computador o utilizam.", "Déjelo sin marcar si otras herramientas de este equipo lo usan.", "このコンピューターの他のツールが使用している場合はチェックを外したままにしてください。", "اتركه بدون تحديد إذا كانت أدوات أخرى على هذا الكمبيوتر تستخدمه.", "Laissez décoché si d'autres outils de cet ordinateur l'utilisent.", "Lascia deselezionato se altri strumenti su questo computer lo usano.", "Оставьте неотмеченным, если им пользуются другие программы на этом компьютере."])),
    "RO_DATA": dict(zip(L, [
        "Your videos and telemetry are NOT touched: the media library folder (library_root) and the Race Studio 3 data stay exactly as they are.",
        "Seus vídeos e telemetria NÃO são tocados: a pasta da biblioteca de mídia (library_root) e os dados do Race Studio 3 permanecem exatamente como estão.",
        "Sus vídeos y telemetría NO se tocan: la carpeta de la biblioteca (library_root) y los datos de Race Studio 3 quedan exactamente como están.",
        "動画とテレメトリには一切触れません: メディア ライブラリ フォルダー (library_root) と Race Studio 3 のデータはそのまま残ります。",
        "لا يتم المساس بفيديوهاتك وبيانات التليمترية: يبقى مجلد مكتبة الوسائط (library_root) وبيانات Race Studio 3 كما هي تماما.",
        "Vos vidéos et votre télémétrie ne sont PAS touchées : le dossier de la médiathèque (library_root) et les données Race Studio 3 restent tels quels.",
        "I tuoi video e la telemetria NON vengono toccati: la cartella della libreria (library_root) e i dati di Race Studio 3 restano esattamente come sono.",
        "Ваши видео и телеметрия НЕ затрагиваются: папка медиатеки (library_root) и данные Race Studio 3 остаются как есть.",
    ])),
}

# Simplified Chinese, merged into S (keeps the base rows above 8 wide).
ZH: dict[str, str] = {
    "BACK": "< 上一步(&B)",
    "NEXT": "下一步(&N) >",
    "CANCELB": "取消",
    "INSTALL": "安装(&I)",
    "FINISH": "完成(&F)",
    "YES": "是(&Y)",
    "NO": "否(&N)",
    "REMOVEB": "删除(&R)",
    "REPAIRB": "修复(&P)",
    "SKIPBTN": "跳过此步骤(&K)",
    "SKIPANYWAY": "仍然跳过(&S)",
    "WELCOME_TITLE": "欢迎使用 MyOverlay 安装向导",
    "WELCOME_DESC": "安装向导会将 MyOverlay（面向 MyChron 用户的终极叠加工具）安装到您的计算机上。单击“下一步”继续，或单击“取消”退出。",
    "LANG_TITLE": "视频语言",
    "LANG_DESC": "选择输出视频的语言。",
    "LANG_EXPLAIN": "所选语言应用于 delta 叠加层标签以及 YouTube 视频的标题和说明。配置文件仍保持英文。",
    "LANG_LABEL": "语言：",
    "GC_TITLE": "Google Cloud SDK",
    "GC_DESC": "在此计算机上未找到 Google Cloud SDK。",
    "GC_EXPLAIN": "它已随 MyOverlay 一起打包，继续时将自动完成设置——静默安装，没有单独的向导，也无需任何点击。安装时会显示进度条；这可能需要一两分钟。\n\n后续步骤中的 Google API 配置依赖于它。",
    "SC_TITLE": "快捷方式",
    "SC_DESC": "选择要创建的快捷方式。",
    "SC_START": "创建“开始”菜单快捷方式",
    "SC_DESKTOP": "创建桌面图标",
    "SC_NOTE": "快捷方式会启动 MyOverlay 的一键式工作流（myoverlay run）。",
    "GA_TITLE": "Google API 配置（YouTube 上传）",
    "GA_DESC": "一次性设置，以便将完成的视频上传到 YouTube（默认为“不公开”）。",
    "GA_WHAT_H": "安装程序将执行的操作",
    "GA_WHAT": "MyOverlay 使用上一步的 Google Cloud SDK 自动为您配置 Google：创建 Google Cloud 项目、启用 YouTube Data API、设置并发布 OAuth 同意屏幕（使访问权限不会在 7 天后过期），并创建桌面 OAuth 客户端——为应用保存其凭据。您无需复制、粘贴或在 Cloud Console 中点击。",
    "GA_WHY_H": "为什么需要它",
    "GA_WHY": "仅在将渲染后的视频上传到 YouTube 时才需要。其他所有功能——摄像头/遥测导入、MyChron 同步和叠加渲染——无需它即可运行。",
    "GA_NEXT_H": "您需要做的",
    "GA_NEXT": "首次运行 MyOverlay 时，会打开一次普通的浏览器窗口。使用拥有您 YouTube 频道的 Google 账号登录——之后窗口会自动关闭，设置也会自动完成。",
    "GA_SKIPNOTE": "如果您不想发布到 YouTube，可以跳过。",
    "SW_TITLE": "警告：将禁用 YouTube 发布",
    "SW_DESC": "您选择了跳过 Google API 配置。",
    "SW_TEXT": "如果没有 Google API 配置，MyOverlay 将不会把渲染后的视频上传到 YouTube（上传默认为“不公开”）。其他所有功能——摄像头/遥测导入、MyChron 同步和叠加渲染——均正常工作。\n\n您可以随时稍后配置：运行“myoverlay google-setup”（会打开相同的一次性浏览器登录），或按照 README 的“YouTube setup”部分操作。",
    "RES_TITLE": "默认输出分辨率",
    "RES_DESC": "选择渲染视频的默认分辨率。",
    "RES_EXPLAIN": "渲染叠加层时，画面会缩放到此高度。稍后可在 config.toml 中更改，或在每次渲染时使用 --resolution 更改。",
    "RES_LABEL": "分辨率：",
    "VR_TITLE": "准备安装",
    "VR_DESC": "单击“安装”开始。单击“上一步”查看或更改您的选择，或单击“取消”退出。",
    "VRM_TITLE": "准备删除",
    "VRM_DESC": "单击“删除”从计算机中删除 MyOverlay。单击“上一步”查看或更改您的选择，或单击“取消”退出。",
    "VRP_TITLE": "准备修复",
    "VRP_DESC": "单击“修复”修复安装。单击“上一步”返回，或单击“取消”退出。",
    "PROG_TITLE": "正在安装 MyOverlay",
    "PROG_WAIT": "安装向导正在执行所请求的操作，请稍候。这可能需要几分钟。",
    "PROG_STATUS": "状态：",
    "EXIT_TITLE": "MyOverlay 安装向导已完成",
    "EXIT_DESC": "单击“完成”退出安装向导。",
    "CANCEL_MSG": "确定要取消 MyOverlay 的安装吗？",
    "MW_TITLE": "MyOverlay 维护",
    "MW_DESC": "安装向导可修复或删除 MyOverlay。单击“下一步”继续，或单击“取消”退出。",
    "MT_TITLE": "修复或删除",
    "MT_DESC": "选择要执行的操作。",
    "MT_REPAIR_DESC": "修复安装（缺失或损坏的文件、快捷方式和注册表项）。",
    "MT_REMOVE_DESC": "从此计算机中删除 MyOverlay。",
    "RO_TITLE": "卸载选项",
    "RO_DESC": "选择要删除的内容。",
    "RO_EXPLAIN": "卸载会删除该软件安装的所有内容：应用程序、其快捷方式及其工作数据（应用的工作副本、config.toml 以及位于 %LOCALAPPDATA%\\myoverlay 下的 Google 凭据）。",
    "RO_GCLOUD": "同时卸载 Google Cloud SDK",
    "RO_GCLOUD_NOTE": "如果此计算机上的其他工具使用它，请不要勾选。",
    "RO_DATA": "您的视频和遥测数据不会被触碰：媒体库文件夹（library_root）和 Race Studio 3 数据将原样保留。",
}
for _key, _zh in ZH.items():
    assert _key in S, f"ZH has unknown key {_key}"
    S[_key]["zh"] = _zh
# fmt: on


def main() -> None:
    for key, per_lang in S.items():
        missing = [lang for lang in LANGS if lang not in per_lang]
        assert not missing, f"{key}: missing {missing}"

    # One JScript object literal per language; \uXXXX escapes keep the file
    # pure ASCII regardless of editor/codepage handling.
    lang_blocks = []
    for lang in LANGS:
        entries = ",\n".join(
            f'    "P_{key}": {json.dumps(per_lang[lang], ensure_ascii=True)}'
            for key, per_lang in S.items()
        )
        lang_blocks.append(f'  "{lang}": {{\n{entries}\n  }}')
    table = "var STRINGS = {\n" + ",\n".join(lang_blocks) + "\n};\n"

    script = (
        "// GENERATED FILE - do not edit. Regenerate with:\n"
        "//   uv run python packaging/msi/gen_i18n_ui.py\n"
        "// MSI immediate CA: fill every P_* UI property with the strings for\n"
        "// the language in OUTPUT_LANGUAGE (fallback English). Runs before the\n"
        "// first dialog and again whenever the language combo changes.\n\n"
        + table
        + "\nfunction ApplyUiLanguage() {\n"
        "    var lang = Session.Property(\"OUTPUT_LANGUAGE\");\n"
        "    var t = STRINGS[lang] || STRINGS[\"en\"];\n"
        "    for (var key in t) {\n"
        "        Session.Property(key) = t[key];\n"
        "    }\n"
        "    return 1;\n"
        "}\n"
    )
    out = Path(__file__).parent / "i18n_ui.js"
    out.write_text(script, encoding="ascii", newline="\r\n")
    n = sum(len(v) for v in S.values())
    print(f"wrote {out} ({len(S)} keys x {len(LANGS)} languages = {n} strings)")


if __name__ == "__main__":
    main()
