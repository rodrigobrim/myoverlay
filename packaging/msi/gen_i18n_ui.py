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

    # -------- component-selection page --------
    "COMP_TITLE": dict(zip(L, ["Choose components to install", "Escolha os componentes a instalar", "Elija los componentes a instalar", "インストールするコンポーネントの選択", "اختر المكونات المراد تثبيتها", "Choisissez les composants à installer", "Scegli i componenti da installare", "Выберите компоненты для установки"])),
    "COMP_DESC":  dict(zip(L, ["MyOverlay bundles the tools below. Some are required and cannot be changed.", "O MyOverlay inclui as ferramentas abaixo. Algumas são obrigatórias e não podem ser alteradas.", "MyOverlay incluye las herramientas siguientes. Algunas son obligatorias y no se pueden cambiar.", "MyOverlay には以下のツールが同梱されています。一部は必須で変更できません。", "يتضمن MyOverlay الأدوات التالية. بعضها مطلوب ولا يمكن تغييره.", "MyOverlay inclut les outils ci-dessous. Certains sont obligatoires et non modifiables.", "MyOverlay include gli strumenti seguenti. Alcuni sono obbligatori e non modificabili.", "MyOverlay включает следующие инструменты. Некоторые обязательны и не могут быть изменены."])),
    "COMP_GROUP": dict(zip(L, ["Bundled software", "Software incluído", "Software incluido", "同梱ソフトウェア", "البرامج المضمنة", "Logiciels inclus", "Software incluso", "Включённое ПО"])),
    "COMP_REQUIRED": dict(zip(L, ["(required)", "(obrigatório)", "(obligatorio)", "(必須)", "(مطلوب)", "(obligatoire)", "(obbligatorio)", "(обязательно)"])),
    "COMP_OPTIONAL": dict(zip(L, ["(optional)", "(opcional)", "(opcional)", "(任意)", "(اختياري)", "(facultatif)", "(facoltativo)", "(необязательно)"])),
    "COMP_GCLOUD_NOTE": dict(zip(L, [
        "The Google Cloud SDK is only needed to upload videos to YouTube. Untick it if you do not plan to publish your videos automatically (unlisted by default). The recommendation is to install it then disable the auto-publish if you desire.",
        "O Google Cloud SDK só é necessário para enviar vídeos ao YouTube. Desmarque se você não pretende publicar seus vídeos automaticamente (não listados por padrão). A recomendação é instalá-lo e depois desativar a publicação automática, se preferir.",
        "El Google Cloud SDK solo es necesario para subir vídeos a YouTube. Desmárquelo si no piensa publicar sus vídeos automáticamente (ocultos por defecto). La recomendación es instalarlo y luego desactivar la publicación automática si lo prefiere.",
        "Google Cloud SDK は YouTube への動画アップロードにのみ必要です。動画を自動的に公開する予定がない場合はチェックを外してください（既定では限定公開）。おすすめはインストールしておき、必要に応じて後で自動公開を無効にすることです。",
        "Google Cloud SDK مطلوب فقط لرفع الفيديوهات إلى YouTube. أزل التحديد إذا كنت لا تنوي نشر فيديوهاتك تلقائيا (غير مدرجة افتراضيا). يوصى بتثبيته ثم تعطيل النشر التلقائي إذا رغبت.",
        "Le Google Cloud SDK n'est nécessaire que pour envoyer des vidéos sur YouTube. Décochez-le si vous ne comptez pas publier vos vidéos automatiquement (non répertoriées par défaut). Il est recommandé de l'installer puis de désactiver la publication automatique si vous le souhaitez.",
        "Il Google Cloud SDK serve solo per caricare video su YouTube. Deseleziona se non intendi pubblicare i tuoi video automaticamente (non in elenco per impostazione predefinita). Si consiglia di installarlo e poi disattivare la pubblicazione automatica se preferisci.",
        "Google Cloud SDK нужен только для загрузки видео на YouTube. Снимите флажок, если не планируете публиковать видео автоматически (по умолчанию — по ссылке). Рекомендуется установить его, а затем при желании отключить автопубликацию.",
    ])),

    # -------- destination page --------
    "DEST_TITLE": dict(zip(L, ["Destination folder", "Pasta de destino", "Carpeta de destino", "インストール先フォルダー", "مجلد الوجهة", "Dossier de destination", "Cartella di destinazione", "Папка назначения"])),
    "DEST_DESC": dict(zip(L, ["Choose where to install MyOverlay.", "Escolha onde instalar o MyOverlay.", "Elija dónde instalar MyOverlay.", "MyOverlay のインストール先を選択してください。", "اختر مكان تثبيت MyOverlay.", "Choisissez où installer MyOverlay.", "Scegli dove installare MyOverlay.", "Выберите, куда установить MyOverlay."])),
    "DEST_EXPLAIN": dict(zip(L, [
        "All bundled tools (FFmpeg, Git and, if selected, the Google Cloud SDK) are installed under this folder. Pick a location with enough free space.",
        "Todas as ferramentas incluídas (FFmpeg, Git e, se selecionado, o Google Cloud SDK) são instaladas nesta pasta. Escolha um local com espaço livre suficiente.",
        "Todas las herramientas incluidas (FFmpeg, Git y, si se selecciona, el Google Cloud SDK) se instalan en esta carpeta. Elija una ubicación con suficiente espacio libre.",
        "同梱ツール（FFmpeg、Git、選択した場合は Google Cloud SDK）はすべてこのフォルダーにインストールされます。十分な空き容量のある場所を選んでください。",
        "يتم تثبيت جميع الأدوات المضمنة (FFmpeg وGit، وGoogle Cloud SDK إذا تم تحديده) داخل هذا المجلد. اختر موقعا به مساحة خالية كافية.",
        "Tous les outils inclus (FFmpeg, Git et, si sélectionné, le Google Cloud SDK) sont installés dans ce dossier. Choisissez un emplacement disposant de suffisamment d'espace libre.",
        "Tutti gli strumenti inclusi (FFmpeg, Git e, se selezionato, il Google Cloud SDK) vengono installati in questa cartella. Scegli una posizione con spazio libero sufficiente.",
        "Все включённые инструменты (FFmpeg, Git и, если выбрано, Google Cloud SDK) устанавливаются в эту папку. Выберите место с достаточным свободным местом.",
    ])),
    "DEST_LABEL": dict(zip(L, ["Install MyOverlay to:", "Instalar o MyOverlay em:", "Instalar MyOverlay en:", "MyOverlay のインストール先:", "تثبيت MyOverlay في:", "Installer MyOverlay dans :", "Installa MyOverlay in:", "Установить MyOverlay в:"])),
    "DEST_BROWSE": dict(zip(L, ["B&rowse...", "&Procurar...", "&Examinar...", "参照(&R)...", "استعراض(&R)...", "&Parcourir...", "S&foglia...", "О&бзор..."])),

    # -------- shortcuts page --------
    "SC_TITLE": dict(zip(L, ["Shortcuts", "Atalhos", "Accesos directos", "ショートカット", "الاختصارات", "Raccourcis", "Collegamenti", "Ярлыки"])),
    "SC_DESC":  dict(zip(L, ["Choose which shortcuts to create.", "Escolha quais atalhos criar.", "Elija qué accesos directos crear.", "作成するショートカットを選択してください。", "اختر الاختصارات التي تريد إنشاءها.", "Choisissez les raccourcis à créer.", "Scegli quali collegamenti creare.", "Выберите, какие ярлыки создать."])),
    "SC_START": dict(zip(L, ["Create a Start Menu shortcut", "Criar atalho no Menu Iniciar", "Crear acceso directo en el menú Inicio", "スタート メニューにショートカットを作成", "إنشاء اختصار في قائمة ابدأ", "Créer un raccourci dans le menu Démarrer", "Crea un collegamento nel menu Start", "Создать ярлык в меню «Пуск»"])),
    "SC_DESKTOP": dict(zip(L, ["Create a Desktop icon", "Criar ícone na Área de Trabalho", "Crear icono en el Escritorio", "デスクトップにアイコンを作成", "إنشاء أيقونة على سطح المكتب", "Créer une icône sur le Bureau", "Crea un'icona sul Desktop", "Создать значок на рабочем столе"])),
    "SC_NOTE": dict(zip(L, ["The shortcuts open MyOverlay.", "Os atalhos abrem o MyOverlay.", "Los accesos directos abren MyOverlay.", "ショートカットは MyOverlay を起動します。", "تفتح الاختصارات MyOverlay.", "Les raccourcis ouvrent MyOverlay.", "I collegamenti aprono MyOverlay.", "Ярлыки открывают MyOverlay."])),

    # -------- resolution page --------
    "RES_TITLE": dict(zip(L, ["Default output resolution", "Resolução padrão de saída", "Resolución de salida predeterminada", "既定の出力解像度", "دقة الإخراج الافتراضية", "Résolution de sortie par défaut", "Risoluzione di output predefinita", "Разрешение вывода по умолчанию"])),
    "RES_DESC": dict(zip(L, ["Choose the default resolution of rendered videos.", "Escolha a resolução padrão dos vídeos renderizados.", "Elija la resolución predeterminada de los vídeos renderizados.", "レンダリングする動画の既定の解像度を選択してください。", "اختر الدقة الافتراضية للفيديوهات المعالجة.", "Choisissez la résolution par défaut des vidéos rendues.", "Scegli la risoluzione predefinita dei video renderizzati.", "Выберите разрешение готовых видео по умолчанию."])),
    "RES_EXPLAIN": dict(zip(L, [
        "Footage is scaled to this height when the overlay is rendered. You can change it later in MyOverlay's settings.",
        "As imagens são redimensionadas para esta altura ao renderizar o overlay. Você pode alterá-la depois nas configurações do MyOverlay.",
        "Las imágenes se escalan a esta altura al renderizar el overlay. Puede cambiarla más tarde en la configuración de MyOverlay.",
        "オーバーレイのレンダリング時に、映像はこの高さにスケーリングされます。後から MyOverlay の設定で変更できます。",
        "يتم تغيير حجم اللقطات إلى هذا الارتفاع عند معالجة الطبقة. يمكنك تغييرها لاحقًا من إعدادات MyOverlay.",
        "Les images sont mises à l'échelle à cette hauteur lors du rendu de l'overlay. Vous pourrez la modifier plus tard dans les paramètres de MyOverlay.",
        "Le riprese vengono scalate a questa altezza durante il rendering dell'overlay. Puoi cambiarla in seguito nelle impostazioni di MyOverlay.",
        "Кадры масштабируются до этой высоты при рендеринге оверлея. Позже это можно изменить в настройках MyOverlay.",
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
    "PROG_GOOGLE_NOTE": dict(zip(L, [
        "A browser window will open near the end of setup for you to sign in to Google. Sign in there, then keep both the browser and this window open until it finishes - this can take a few minutes.",
        "Perto do fim da instalação, uma janela do navegador será aberta para você entrar na sua conta do Google. Faça login nela e mantenha o navegador e esta janela abertos até o processo terminar - isso pode levar alguns minutos.",
        "Cerca del final de la instalación, se abrirá una ventana del navegador para que inicie sesión en Google. Inicie sesión allí y mantenga abiertos el navegador y esta ventana hasta que el proceso termine - esto puede tardar varios minutos.",
        "セットアップの終盤に、Google にサインインするためのブラウザー ウィンドウが開きます。そこでサインインし、処理が終わるまでブラウザーとこのウィンドウの両方を開いたままにしてください。数分かかることがあります。",
        "بالقرب من نهاية الإعداد، ستُفتح نافذة متصفح لتسجيل الدخول إلى Google. سجل الدخول هناك، واترك المتصفح وهذه النافذة مفتوحين حتى تنتهي العملية - قد يستغرق ذلك بضع دقائق.",
        "Vers la fin de l'installation, une fenêtre de navigateur s'ouvrira pour vous permettre de vous connecter à Google. Connectez-vous, puis laissez le navigateur et cette fenêtre ouverts jusqu'à la fin du processus - cela peut prendre quelques minutes.",
        "Verso la fine dell'installazione si aprirà una finestra del browser per accedere a Google. Accedi lì, quindi lascia aperti sia il browser sia questa finestra finché il processo non termina: l'operazione può richiedere alcuni minuti.",
        "Ближе к концу установки откроется окно браузера для входа в аккаунт Google. Войдите там, затем оставьте открытыми окно браузера и это окно до завершения процесса - это может занять несколько минут.",
    ])),
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
        "Uninstalling removes MyOverlay: the application, its shortcuts, and its settings and saved sign-in.",
        "A desinstalação remove o MyOverlay: o aplicativo, seus atalhos e suas configurações e o login salvo.",
        "La desinstalación elimina MyOverlay: la aplicación, sus accesos directos y su configuración e inicio de sesión guardado.",
        "アンインストールすると MyOverlay を削除します: アプリケーション、ショートカット、設定、保存されたサインイン情報。",
        "تؤدي إزالة التثبيت إلى إزالة MyOverlay: التطبيق واختصاراته وإعداداته وتسجيل الدخول المحفوظ.",
        "La désinstallation supprime MyOverlay : l'application, ses raccourcis, ses paramètres et la connexion enregistrée.",
        "La disinstallazione rimuove MyOverlay: l'applicazione, i suoi collegamenti, le sue impostazioni e l'accesso salvato.",
        "Удаление убирает MyOverlay: приложение, его ярлыки, настройки и сохранённый вход.",
    ])),
    "RO_GCLOUD": dict(zip(L, ["Also uninstall the Google Cloud SDK", "Desinstalar também o Google Cloud SDK", "Desinstalar también el Google Cloud SDK", "Google Cloud SDK もアンインストールする", "إزالة Google Cloud SDK أيضا", "Désinstaller aussi le Google Cloud SDK", "Disinstalla anche il Google Cloud SDK", "Также удалить Google Cloud SDK"])),
    "RO_GCLOUD_NOTE": dict(zip(L, ["Leave unchecked if other tools on this computer use it.", "Deixe desmarcado se outras ferramentas deste computador o utilizam.", "Déjelo sin marcar si otras herramientas de este equipo lo usan.", "このコンピューターの他のツールが使用している場合はチェックを外したままにしてください。", "اتركه بدون تحديد إذا كانت أدوات أخرى على هذا الكمبيوتر تستخدمه.", "Laissez décoché si d'autres outils de cet ordinateur l'utilisent.", "Lascia deselezionato se altri strumenti su questo computer lo usano.", "Оставьте неотмеченным, если им пользуются другие программы на этом компьютере."])),
    "RO_DATA": dict(zip(L, [
        "Your videos and telemetry are not touched: your media library and Race Studio 3 data stay exactly as they are.",
        "Seus vídeos e telemetria não são afetados: sua biblioteca de mídia e os dados do Race Studio 3 permanecem exatamente como estão.",
        "Sus vídeos y telemetría no se ven afectados: su biblioteca multimedia y los datos de Race Studio 3 quedan exactamente como están.",
        "動画とテレメトリには一切触れません: メディア ライブラリと Race Studio 3 のデータはそのまま残ります。",
        "لا يتم المساس بفيديوهاتك وبيانات القياس عن بعد: تبقى مكتبة الوسائط وبيانات Race Studio 3 كما هي تمامًا.",
        "Vos vidéos et votre télémétrie ne sont pas touchées : votre médiathèque et les données Race Studio 3 restent telles quelles.",
        "I tuoi video e la telemetria non vengono toccati: la tua libreria multimediale e i dati di Race Studio 3 restano esattamente come sono.",
        "Ваши видео и телеметрия не затрагиваются: ваша медиатека и данные Race Studio 3 остаются как есть.",
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
    "WELCOME_TITLE": "欢迎使用 MyOverlay 安装向导",
    "WELCOME_DESC": "安装向导会将 MyOverlay（面向 MyChron 用户的终极叠加工具）安装到您的计算机上。单击“下一步”继续，或单击“取消”退出。",
    "LANG_TITLE": "视频语言",
    "LANG_DESC": "选择输出视频的语言。",
    "LANG_EXPLAIN": "所选语言应用于 delta 叠加层标签以及 YouTube 视频的标题和说明。配置文件仍保持英文。",
    "LANG_LABEL": "语言：",
    "COMP_TITLE": "选择要安装的组件",
    "COMP_DESC": "MyOverlay 捆绑了以下工具。部分为必需项，无法更改。",
    "COMP_GROUP": "捆绑的软件",
    "COMP_REQUIRED": "（必需）",
    "COMP_OPTIONAL": "（可选）",
    "COMP_GCLOUD_NOTE": "Google Cloud SDK 仅用于将视频上传到 YouTube。如果您不打算自动发布视频（默认不公开），请取消勾选。建议先安装它，之后可根据需要禁用自动发布。",
    "DEST_TITLE": "目标文件夹",
    "DEST_DESC": "选择 MyOverlay 的安装位置。",
    "DEST_EXPLAIN": "所有捆绑工具（FFmpeg、Git，以及如已选择的 Google Cloud SDK）都会安装到此文件夹下。请选择有足够可用空间的位置。",
    "DEST_LABEL": "将 MyOverlay 安装到：",
    "DEST_BROWSE": "浏览(&R)...",
    "SC_TITLE": "快捷方式",
    "SC_DESC": "选择要创建的快捷方式。",
    "SC_START": "创建“开始”菜单快捷方式",
    "SC_DESKTOP": "创建桌面图标",
    "SC_NOTE": "快捷方式用于打开 MyOverlay。",
    "RES_TITLE": "默认输出分辨率",
    "RES_DESC": "选择渲染视频的默认分辨率。",
    "RES_EXPLAIN": "渲染叠加层时，画面会缩放到此高度。您可以稍后在 MyOverlay 的设置中更改。",
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
    "PROG_GOOGLE_NOTE": "安装即将结束时，会打开一个浏览器窗口供您登录 Google 账号。请在其中登录，并保持浏览器和此窗口处于打开状态，直到该过程完成——这可能需要几分钟。",
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
    "RO_EXPLAIN": "卸载会删除 MyOverlay：应用程序、其快捷方式，以及其设置和已保存的登录信息。",
    "RO_GCLOUD": "同时卸载 Google Cloud SDK",
    "RO_GCLOUD_NOTE": "如果此计算机上的其他工具使用它，请不要勾选。",
    "RO_DATA": "您的视频和遥测数据不会被触碰：您的媒体库和 Race Studio 3 数据将原样保留。",
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
