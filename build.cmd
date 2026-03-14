@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SERVER_VERSION=%SERVER_VERSION%"
if not defined SERVER_VERSION set "SERVER_VERSION=1.21.11"

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "AGENT_CONFIG_DIR=%SCRIPT_DIR%\configuration"
set "BUILD_DIR=%SCRIPT_DIR%\build"
set "JAR_PATH=%BUILD_DIR%\server.jar"
set "META_INF_PATH=%BUILD_DIR%\META-INF"
set "BINARY_NAME=native-minecraft-server"
set "SELFMAIN_BUILD_DIR=%BUILD_DIR%\selfmain"
set "SELFMAIN_CLASS=SelfMain"
set "ORIGINAL_SPIGOT_JAR=%SCRIPT_DIR%\versions\%SERVER_VERSION%\spigot-%SERVER_VERSION%.jar"
set "LIBRARIES_DIR=%META_INF_PATH%\libraries"
set "LIBRARIES_LIST=%META_INF_PATH%\libraries.list"

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
    echo Extracting resources from Minecraft's server.jar with jar.exe...
    pushd "%BUILD_DIR%" || exit /b 1
    "%GRAALVM_HOME%\bin\jar.exe" xf "%JAR_PATH%" META-INF || exit /b 1
    popd || exit /b 1
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

REM Non-Paperclip scenario: patch the exact spigot jar that native-image will use.
REM Prefer build\META-INF\versions\...\spigot-*.jar because current Windows native-image.args
REM shows that jar is on the classpath. Fall back to build\versions\...\spigot-*.jar.
if /i not "%JAR_MAIN_CLASS%"=="io.papermc.paperclip.Main" (
    set "TARGET_SPIGOT_JAR=%META_INF_PATH%\versions\%SERVER_VERSION%\spigot-%SERVER_VERSION%.jar"
    if not exist "!TARGET_SPIGOT_JAR!" set "TARGET_SPIGOT_JAR=%BUILD_DIR%\versions\%SERVER_VERSION%\spigot-%SERVER_VERSION%.jar"

    if exist "!TARGET_SPIGOT_JAR!" (
        echo Detected direct spigot jar used for build: !TARGET_SPIGOT_JAR!

        if exist "%BUILD_DIR%\patch" rmdir /s /q "%BUILD_DIR%\patch"
        mkdir "%BUILD_DIR%\patch\jar" >nul 2>&1
        copy /y "%SCRIPT_DIR%\work\patch_sleep.py" "%BUILD_DIR%\patch\patch_sleep.py" >nul

        echo Extracting CraftBukkit Main.class from target spigot jar...
        pushd "%BUILD_DIR%\patch\jar" || exit /b 1
        "%GRAALVM_HOME%\bin\jar.exe" xf "!TARGET_SPIGOT_JAR!" org/bukkit/craftbukkit/Main.class || exit /b 1
        popd || exit /b 1

        echo Patching CraftBukkit Main.class to remove outdated-build sleep...
        python "%BUILD_DIR%\patch\patch_sleep.py" "%BUILD_DIR%\patch\jar\org\bukkit\craftbukkit\Main.class" || exit /b 1
        echo Verifying patched Main.class sleep delay constant is neutralized...
        python "%BUILD_DIR%\patch\patch_sleep.py" "%BUILD_DIR%\patch\jar\org\bukkit\craftbukkit\Main.class" --verify-only || exit /b 1

        echo Updating target spigot jar with modified Main.class...
        pushd "%BUILD_DIR%\patch\jar" || exit /b 1
        "%GRAALVM_HOME%\bin\jar.exe" uf "!TARGET_SPIGOT_JAR!" org/bukkit/craftbukkit/Main.class || exit /b 1
        popd || exit /b 1

        echo Verifying updated target spigot jar really contains patched Main.class...
        python "%BUILD_DIR%\patch\patch_sleep.py" "!TARGET_SPIGOT_JAR!" --in-jar --verify-only || exit /b 1

        REM Compile SelfMain with the same classpath, then make it the native-image entrypoint.
        if exist "%SELFMAIN_BUILD_DIR%" rmdir /s /q "%SELFMAIN_BUILD_DIR%"
        mkdir "%SELFMAIN_BUILD_DIR%" >nul 2>&1
        echo Compiling SelfMain.java...
        "%GRAALVM_HOME%\bin\javac.exe" -cp "!TARGET_SPIGOT_JAR!;!CLASSPATH_JOINED!" -d "%SELFMAIN_BUILD_DIR%" "%SCRIPT_DIR%\work\SelfMain.java" || exit /b 1
        if not exist "%SELFMAIN_BUILD_DIR%\SelfMain.class" (
            echo SelfMain compilation failed: %SELFMAIN_BUILD_DIR%\SelfMain.class not found
            exit /b 1
        )
        set "CLASSPATH_JOINED=%SELFMAIN_BUILD_DIR%;!TARGET_SPIGOT_JAR!;!CLASSPATH_JOINED!"
        set "MAIN_CLASS=%SELFMAIN_CLASS%"
        echo Using SelfMain for native image: !MAIN_CLASS!
    )
)

if /i "%JAR_MAIN_CLASS%"=="io.papermc.paperclip.Main" (
    echo Detected Paperclip server jar.

    if exist "%GRAALVM_HOME%\bin\javap.exe" (
        python "%SCRIPT_DIR%\work\decompile_main.py" --javap "%GRAALVM_HOME%\bin\javap.exe" --jar "%JAR_PATH%" --out "%BUILD_DIR%\decompile_main_serverjar.txt" >nul 2>&1
    )

    set "BUILD_VERSIONS_SPIGOT_JAR=%BUILD_DIR%\versions\%SERVER_VERSION%\spigot-%SERVER_VERSION%.jar"
    if exist "!BUILD_VERSIONS_SPIGOT_JAR!" set "PATCHED_JAR=!BUILD_VERSIONS_SPIGOT_JAR!"

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

    if exist "%BUILD_DIR%\patch" rmdir /s /q "%BUILD_DIR%\patch"
    mkdir "%BUILD_DIR%\patch\jar" >nul 2>&1

    copy /y "%SCRIPT_DIR%\work\patch_sleep.py" "%BUILD_DIR%\patch\patch_sleep.py" >nul

    echo Patching CraftBukkit Main.class inside patched jar to remove outdated-build sleep...
    python "%BUILD_DIR%\patch\patch_sleep.py" "!PATCHED_JAR!" --in-jar || exit /b 1
    echo Verifying patched jar contains sleep delay constant patched to 0L...
    python "%BUILD_DIR%\patch\patch_sleep.py" "!PATCHED_JAR!" --in-jar --verify-only || exit /b 1

    if exist "%GRAALVM_HOME%\bin\javap.exe" (
        python "%SCRIPT_DIR%\work\decompile_main.py" --javap "%GRAALVM_HOME%\bin\javap.exe" --jar "!PATCHED_JAR!" --out "%BUILD_DIR%\decompile_main_patchedjar.txt" >nul 2>&1
    )

    echo Using patched jar on classpath: !PATCHED_JAR!
    REM Compile SelfMain.java against the full runtime classpath and use it as entrypoint.
    if exist "%SELFMAIN_BUILD_DIR%" rmdir /s /q "%SELFMAIN_BUILD_DIR%"
    mkdir "%SELFMAIN_BUILD_DIR%" >nul 2>&1
    echo Compiling SelfMain.java...
    "%GRAALVM_HOME%\bin\javac.exe" -cp "!PATCHED_JAR!;!CLASSPATH_JOINED!" -d "%SELFMAIN_BUILD_DIR%" "%SCRIPT_DIR%\work\SelfMain.java" || exit /b 1
    if not exist "%SELFMAIN_BUILD_DIR%\SelfMain.class" (
        echo SelfMain compilation failed: %SELFMAIN_BUILD_DIR%\SelfMain.class not found
        exit /b 1
    )
    set "CLASSPATH_JOINED=%SELFMAIN_BUILD_DIR%;!PATCHED_JAR!;!CLASSPATH_JOINED!"
    set "MAIN_CLASS=%SELFMAIN_CLASS%"
    echo Using SelfMain for native image: !MAIN_CLASS!

    set "PATCHED_JAR_REL="
    if /i not "!PATCHED_JAR:%BUILD_DIR%\=!"=="!PATCHED_JAR!" (
        set "PATCHED_JAR_REL=!PATCHED_JAR:%BUILD_DIR%\=!"
        set "PATCHED_JAR_REL=!PATCHED_JAR_REL:\=/!"
    )
    if defined PATCHED_JAR_REL (
        echo Patched jar relative path ^(for resources^): !PATCHED_JAR_REL!
    ) else (
        echo Patched jar is outside BUILD_DIR, skipping IncludeResources embedding: !PATCHED_JAR!
    )
)

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

REM SelfMain output dir is created in the branch that compiles it. Do not touch it here.

if exist "%BUILD_DIR%\native-image.args" del /f /q "%BUILD_DIR%\native-image.args"

set "EXTRA_NI_RESOURCE_ARG="
if defined PATCHED_JAR_REL set "EXTRA_NI_RESOURCE_ARG=-H:IncludeResources=\\Q!PATCHED_JAR_REL!\\E"

echo Launching native-image build...
call "%NI_EXEC%" ^
    -H:+UnlockExperimentalVMOptions ^
    --no-fallback ^
    -H:ConfigurationFileDirectories=%AGENT_CONFIG_DIR_ARG% ^
    -H:IncludeResources=\\Qjoptsimple/HelpFormatterMessages.properties\\E ^
    -H:IncludeResources=\\Qjoptsimple/ExceptionMessages.properties\\E ^
    -H:+AddAllCharsets ^
    -H:+ReportExceptionStackTraces ^
    --enable-url-protocols=https ^
    --initialize-at-run-time=io.netty ^
    --enable-monitoring=heapdump,jfr ^
    --enable-native-access=ALL-UNNAMED ^
    -H:+SharedArenaSupport ^
    --initialize-at-build-time=net.minecraft.util.profiling.jfr.event ^
    -Dnet.minecraft.util.profiling.jfr.JvmProfiler.ENABLED=false ^
    -Dnet.minecraft.util.profiling.jfr.jfrProfiler=false ^
    -Djdk.jfr.enabled=false ^
    --initialize-at-run-time=net.minecraft.util.profiling.jfr ^
    --initialize-at-run-time=org.apache.logging.log4j ^
    --initialize-at-run-time=joptsimple ^
    --initialize-at-run-time=org.apache.logging.log4j.core.util.DefaultShutdownCallbackRegistry ^
    -H:Name=%BINARY_NAME% ^
    -cp "%CLASSPATH_JOINED_ARG%" ^
    %EXTRA_NI_RESOURCE_ARG% ^
    %* ^
    "%MAIN_CLASS%"
if errorlevel 1 exit /b %errorlevel%

if not exist "%META_INF_PATH%\%BINARY_NAME%.exe" (
    echo native-image finished but did not produce %META_INF_PATH%\%BINARY_NAME%.exe
    exit /b 1
)

@REM if exist "%META_INF_PATH%\%BINARY_NAME%.exe" (
@REM     copy /y "%META_INF_PATH%\%BINARY_NAME%.exe" "%SCRIPT_DIR%\%BINARY_NAME%.exe" >nul
@REM )

popd
popd

echo.
echo Done! The native Minecraft server is located at:
if exist "%META_INF_PATH%\%BINARY_NAME%.exe" (
    echo %META_INF_PATH%\%BINARY_NAME%.exe
) else (
    echo %SCRIPT_DIR%\%BINARY_NAME%.exe
)

exit /b 0
