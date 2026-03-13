@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Build a native-image with a self-controlled entrypoint (SelfMain)
rem - Ensures eula.txt contains eula=true at runtime
rem - Delegates to org.bukkit.craftbukkit.Main

set "SERVER_VERSION=%SERVER_VERSION%"
if not defined SERVER_VERSION set "SERVER_VERSION=1.21.11"

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "AGENT_CONFIG_DIR=%SCRIPT_DIR%\configuration"
set "BUILD_DIR=%SCRIPT_DIR%\build"
set "WORK_DIR=%SCRIPT_DIR%\work"
set "JAR_PATH=%BUILD_DIR%\server.jar"
set "ZIP_PATH=%BUILD_DIR%\server.zip"
set "META_INF_PATH=%BUILD_DIR%\META-INF"
set "BINARY_NAME=native-minecraft-server-self"
set "ORIGINAL_SPIGOT_JAR=%SCRIPT_DIR%\versions\%SERVER_VERSION%\spigot-%SERVER_VERSION%.jar"
set "LIBRARIES_DIR=%META_INF_PATH%\libraries"
set "LIBRARIES_LIST=%META_INF_PATH%\libraries.list"

set "SELF_MAIN_SRC=%WORK_DIR%\SelfMain.java"
set "SELF_CLASSES_DIR=%BUILD_DIR%\self-classes"
set "SELF_JAR=%BUILD_DIR%\self-main.jar"
set "SELF_MAIN_CLASS=SelfMain"

if not defined GRAALVM_HOME (
    echo $GRAALVM_HOME is not set. Please provide a GraalVM installation. Exiting...
    exit /b 1
)

for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=$env:GRAALVM_HOME; if ($null -ne $p) { $p.Trim() }"`) do set "GRAALVM_HOME=%%I"

set "NI_EXEC=%GRAALVM_HOME%\bin\native-image.cmd"
echo Using native-image: %NI_EXEC%

if not exist "%NI_EXEC%" (
    echo native-image.cmd was not found under %GRAALVM_HOME%\bin. Exiting...
    exit /b 1
)

if not exist "%SELF_MAIN_SRC%" (
    echo Missing %SELF_MAIN_SRC%. Exiting...
    exit /b 1
)

if not exist "%BUILD_DIR%" mkdir "%BUILD_DIR%"
pushd "%BUILD_DIR%" || exit /b 1

if not exist "%JAR_PATH%" (
    echo Downloading Minecraft's server.jar...

    set "SERVER_MANIFEST_URL="
    for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; $serverVersion='%SERVER_VERSION%'; $manifest=Invoke-RestMethod -Uri 'https://piston-meta.mojang.com/mc/game/version_manifest.json'; (($manifest.versions ^| Where-Object { $_.id -eq $serverVersion } ^| Select-Object -First 1).url)"`) do set "SERVER_MANIFEST_URL=%%I"
    if not defined SERVER_MANIFEST_URL (
        echo Unable to find manifest url for SERVER_VERSION=%SERVER_VERSION%. Exiting...
        exit /b 1
    )

    set "SERVER_JAR_DL="
    for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; $manifest=Invoke-RestMethod -Uri '%SERVER_MANIFEST_URL%'; $manifest.downloads.server.url"`) do set "SERVER_JAR_DL=%%I"
    if not defined SERVER_JAR_DL (
        echo Unable to find server.jar download url for SERVER_VERSION=%SERVER_VERSION%. Exiting...
        exit /b 1
    )

    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%SERVER_JAR_DL%' -OutFile '%JAR_PATH%'" || exit /b 1
)

if not exist "%META_INF_PATH%" (
    if exist "%ZIP_PATH%" del /f /q "%ZIP_PATH%"
    copy /y "%JAR_PATH%" "%ZIP_PATH%" >nul || exit /b 1
    echo Extracting resources from Minecraft's server.jar...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%ZIP_PATH%' -DestinationPath '%BUILD_DIR%' -Force" || exit /b 1
)

set "CLASSPATH_JOINED="
if not exist "%META_INF_PATH%\classpath-joined" (
    if not exist "%LIBRARIES_LIST%" (
        echo Unable to determine classpath ^(missing classpath-joined and libraries.list^). Exiting...
        exit /b 1
    )

    echo Reconstructing classpath from META-INF/libraries.list...
    for /f "tokens=3" %%J in (%LIBRARIES_LIST%) do (
        set "JAR_NAME=%%J"
        set "JAR_FILE=%LIBRARIES_DIR%\!JAR_NAME!"
        if exist "!JAR_FILE!" (
            if defined CLASSPATH_JOINED (
                set "CLASSPATH_JOINED=!CLASSPATH_JOINED!;!JAR_FILE!"
            ) else (
                set "CLASSPATH_JOINED=!JAR_FILE!"
            )
        )
    )

    if defined CLASSPATH_JOINED (
        set "CLASSPATH_JOINED=!CLASSPATH_JOINED!;%JAR_PATH%"
    ) else (
        set "CLASSPATH_JOINED=%JAR_PATH%"
    )
) else (
    for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-Content -LiteralPath '%META_INF_PATH%\classpath-joined' -Raw).Trim()"`) do set "CLASSPATH_JOINED=%%I"
)

set "JAR_MAIN_CLASS="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; $zip=[System.IO.Compression.ZipFile]::OpenRead('%JAR_PATH%'); try { $entry=$zip.GetEntry('META-INF/MANIFEST.MF'); if($entry){ $sr=New-Object System.IO.StreamReader($entry.Open()); try { $text=$sr.ReadToEnd(); if($text -match '(?m)^Main-Class:\s*(.+)\r?$'){ $matches[1].Trim() } } finally { $sr.Dispose() } } } finally { $zip.Dispose() }"`) do set "JAR_MAIN_CLASS=%%I"

set "MAIN_CLASS=%JAR_MAIN_CLASS%"
if not defined MAIN_CLASS if exist "%META_INF_PATH%\main-class" (
    for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-Content -LiteralPath '%META_INF_PATH%\main-class' -Raw).Trim()"`) do set "MAIN_CLASS=%%I"
)

if not defined MAIN_CLASS (
    echo Unable to determine main class. Exiting...
    exit /b 1
)

pushd "%META_INF_PATH%" || exit /b 1

set "PATCHED_JAR="
set "PATCHED_JAR_REL="

if /i "%JAR_MAIN_CLASS%"=="io.papermc.paperclip.Main" (
    echo Detected Paperclip server jar.

    for /r "%META_INF_PATH%\versions" %%I in (spigot-*.jar) do if not defined PATCHED_JAR set "PATCHED_JAR=%%~fI"

    if defined PATCHED_JAR (
        echo Found existing patched jar, skipping Paperclip materialization: !PATCHED_JAR!
    ) else (
        echo Materializing patched mojang jar before native-image...
        if exist "%GRAALVM_HOME%\bin\java.exe" (
            "%GRAALVM_HOME%\bin\java.exe" -jar "%JAR_PATH%" --version >nul 2>&1
        ) else (
            call "%GRAALVM_HOME%\bin\java.cmd" -jar "%JAR_PATH%" --version >nul 2>&1
        )
        for /r "%META_INF_PATH%\versions" %%I in (spigot-*.jar) do if not defined PATCHED_JAR set "PATCHED_JAR=%%~fI"
    )

    if not defined PATCHED_JAR (
        for /r "%META_INF_PATH%\versions" %%I in (*.jar) do if not defined PATCHED_JAR set "PATCHED_JAR=%%~fI"
    )
    if not defined PATCHED_JAR (
        for /r "%META_INF_PATH%" %%I in (mojang_*.jar) do if not defined PATCHED_JAR set "PATCHED_JAR=%%~fI"
    )

    if not defined PATCHED_JAR (
        echo Paperclip did not produce a patched jar under %META_INF_PATH% ^(preferred recursive versions\spigot-*.jar, then versions\*.jar, then mojang_*.jar^). Exiting...
        exit /b 1
    )

    if exist "%ORIGINAL_SPIGOT_JAR%" (
        if exist "%BUILD_DIR%\patch" rmdir /s /q "%BUILD_DIR%\patch"
        mkdir "%BUILD_DIR%\patch\jar" >nul 2>&1

        echo Extracting clean CraftBukkit Main.class from original spigot jar...
        pushd "%BUILD_DIR%\patch\jar" || exit /b 1
        "%GRAALVM_HOME%\bin\jar.exe" xf "%ORIGINAL_SPIGOT_JAR%" org/bukkit/craftbukkit/Main.class || exit /b 1
        popd || exit /b 1

        copy /y "%SCRIPT_DIR%\work\patch_sleep.py" "%BUILD_DIR%\patch\patch_sleep.py" >nul
        echo Patching CraftBukkit Main.class to remove outdated-build sleep...
        python "%BUILD_DIR%\patch\patch_sleep.py" "%BUILD_DIR%\patch\jar\org\bukkit\craftbukkit\Main.class" || exit /b 1

        echo Updating patched jar with modified Main.class...
        pushd "%BUILD_DIR%\patch\jar" || exit /b 1
        "%GRAALVM_HOME%\bin\jar.exe" uf "!PATCHED_JAR!" org/bukkit/craftbukkit/Main.class || exit /b 1
        popd || exit /b 1
    )

    echo Using patched jar on classpath: !PATCHED_JAR!
    set "CLASSPATH_JOINED=!PATCHED_JAR!;!CLASSPATH_JOINED!"
    set "MAIN_CLASS=org.bukkit.craftbukkit.Main"
    echo Using direct main class for server: !MAIN_CLASS!

    set "PATCHED_JAR_REL=META-INF/versions/%SERVER_VERSION%/spigot-%SERVER_VERSION%.jar"
    if not exist "%BUILD_DIR%\META-INF\versions\%SERVER_VERSION%\spigot-%SERVER_VERSION%.jar" (
        for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$buildDir = [System.IO.Path]::GetFullPath('%BUILD_DIR%'); $patchedJar = [System.IO.Path]::GetFullPath('!PATCHED_JAR!'); [System.IO.Path]::GetRelativePath($buildDir, $patchedJar).Replace('\','/').Trim()"`) do set "PATCHED_JAR_REL=%%I"
    )
    if defined PATCHED_JAR_REL (
        echo Patched jar relative path ^(for resources^): !PATCHED_JAR_REL!
    ) else (
        echo Failed to compute patched jar relative path. Exiting...
        exit /b 1
    )
)

rem --------------------------------------------------------------------------
rem Build SelfMain into a tiny jar and prepend it to the classpath
rem --------------------------------------------------------------------------
echo Building %SELF_MAIN_CLASS%...
if exist "%SELF_CLASSES_DIR%" rmdir /s /q "%SELF_CLASSES_DIR%"
mkdir "%SELF_CLASSES_DIR%" || exit /b 1

rem Compile with server classpath available (so org.bukkit.craftbukkit.Main resolves)
"%GRAALVM_HOME%\bin\javac.exe" -encoding UTF-8 -cp "%CLASSPATH_JOINED%" -d "%SELF_CLASSES_DIR%" "%SELF_MAIN_SRC%"
if errorlevel 1 exit /b %errorlevel%

if exist "%SELF_JAR%" del /f /q "%SELF_JAR%"
"%GRAALVM_HOME%\bin\jar.exe" --create --file "%SELF_JAR%" -C "%SELF_CLASSES_DIR%" .
if errorlevel 1 exit /b %errorlevel%

set "CLASSPATH_JOINED=%SELF_JAR%;%CLASSPATH_JOINED%"
echo Self jar added to classpath: %SELF_JAR%

set "JNA_TMPDIR=%META_INF_PATH%\.jna"
if not exist "%JNA_TMPDIR%" mkdir "%JNA_TMPDIR%"
set "TMPDIR=%JNA_TMPDIR%"
set "JNA_TMPDIR=%JNA_TMPDIR%"
set "JNA_NOSYS=true"

set "AGENT_CONFIG_DIR_ARG=%AGENT_CONFIG_DIR:\=/%"
set "CLASSPATH_JOINED_ARG=%CLASSPATH_JOINED:\=/%"

if exist "%META_INF_PATH%\%BINARY_NAME%.exe" (
    echo Removing existing native-image output to force full rebuild...
    del /f /q "%META_INF_PATH%\%BINARY_NAME%.exe"
)
if exist "%SCRIPT_DIR%\%BINARY_NAME%.exe" (
    del /f /q "%SCRIPT_DIR%\%BINARY_NAME%.exe"
)

set "NI_ARG_FILE=%BUILD_DIR%\native-image-self.args"
if exist "%NI_ARG_FILE%" del /f /q "%NI_ARG_FILE%"

(
    echo --no-fallback
    echo -H:ConfigurationFileDirectories=%AGENT_CONFIG_DIR_ARG%
    echo -H:IncludeResources=\\Qjoptsimple/HelpFormatterMessages.properties\\E
    echo -H:IncludeResources=\\Qjoptsimple/ExceptionMessages.properties\\E
    echo -H:+AddAllCharsets
    echo -H:+ReportExceptionStackTraces
    echo --enable-url-protocols=https
    echo --initialize-at-run-time=io.netty
    echo --enable-monitoring=heapdump,jfr
    echo --enable-native-access=ALL-UNNAMED
    echo -H:+SharedArenaSupport
    echo --initialize-at-build-time=net.minecraft.util.profiling.jfr.event
    echo -Dnet.minecraft.util.profiling.jfr.JvmProfiler.ENABLED=false
    echo -Dnet.minecraft.util.profiling.jfr.jfrProfiler=false
    echo -Djdk.jfr.enabled=false
    echo --initialize-at-run-time=net.minecraft.util.profiling.jfr
    echo --initialize-at-run-time=org.apache.logging.log4j
    echo --initialize-at-run-time=joptsimple
    echo --initialize-at-run-time=org.apache.logging.log4j.core.util.DefaultShutdownCallbackRegistry
    echo -H:Name=%BINARY_NAME%
    echo -cp
    echo %CLASSPATH_JOINED_ARG%
    if defined PATCHED_JAR_REL echo -H:IncludeResources=\\Q%PATCHED_JAR_REL%\\E
) > "%NI_ARG_FILE%"

echo Launching native-image build ^(SelfMain entrypoint^)...
echo Native-image args file: %NI_ARG_FILE%
call "%NI_EXEC%" @"%NI_ARG_FILE%" %* "%SELF_MAIN_CLASS%"
if errorlevel 1 exit /b %errorlevel%

if not exist "%META_INF_PATH%\%BINARY_NAME%.exe" (
    echo native-image finished but did not produce %META_INF_PATH%\%BINARY_NAME%.exe
    exit /b 1
)

if exist "%META_INF_PATH%\%BINARY_NAME%.exe" (
    copy /y "%META_INF_PATH%\%BINARY_NAME%.exe" "%SCRIPT_DIR%\%BINARY_NAME%.exe" >nul
)

popd
popd

echo.
echo Done! The self-entry native Minecraft server is located at:
if exist "%META_INF_PATH%\%BINARY_NAME%.exe" (
    echo %META_INF_PATH%\%BINARY_NAME%.exe
) else (
    echo %SCRIPT_DIR%\%BINARY_NAME%.exe
)

exit /b 0
