#!/usr/bin/env bash

set -o errexit
set -o nounset

SERVER_VERSION="${SERVER_VERSION:-"1.21.11"}"
SERVER_MANIFEST_URL=""
SERVER_JAR_DL=""
SCRIPT_DIR=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )
AGENT_CONFIG_DIR="${SCRIPT_DIR}/configuration/"
BUILD_DIR="${SCRIPT_DIR}/build"
JAR_PATH="${BUILD_DIR}/server.jar"
META_INF_PATH="${BUILD_DIR}/META-INF"
BINARY_NAME="native-minecraft-server"
NI_EXEC="${GRAALVM_HOME:-}/bin/native-image"
LIBRARIES_DIR="${META_INF_PATH}/libraries"
LIBRARIES_LIST="${META_INF_PATH}/libraries.list"
readonly SERVER_VERSION SERVER_MANIFEST_URL SERVER_JAR_DL SCRIPT_DIR AGENT_CONFIG_DIR BUILD_DIR JAR_PATH META_INF_PATH BINARY_NAME NI_EXEC LIBRARIES_DIR LIBRARIES_LIST

if [[ -z "${GRAALVM_HOME:-}" ]]; then
    echo "\$GRAALVM_HOME is not set. Please provide a GraalVM installation. Exiting..."
    exit 1
fi

if ! command -v "${NI_EXEC}" &> /dev/null; then
    echo "Installing GraalVM Native Image..."
    "${GRAALVM_HOME}/bin/gu" install --no-progress native-image
fi

TIMEOUT_BIN="$(command -v timeout || true)"
MATERIALIZE_TIMEOUT_SECONDS="${MATERIALIZE_TIMEOUT_SECONDS:-120}"
PATCH_TIMEOUT_SECONDS="${PATCH_TIMEOUT_SECONDS:-120}"
readonly TIMEOUT_BIN MATERIALIZE_TIMEOUT_SECONDS PATCH_TIMEOUT_SECONDS

run_with_optional_timeout() {
    local timeout_seconds="$1"
    shift
    if [[ -n "${TIMEOUT_BIN}" ]]; then
        "${TIMEOUT_BIN}" "${timeout_seconds}" "$@"
    else
        "$@"
    fi
}

if [[ ! -d "${BUILD_DIR}" ]]; then
    mkdir "${BUILD_DIR}"
fi
pushd "${BUILD_DIR}" > /dev/null

if [[ ! -f "${JAR_PATH}" ]]; then
    echo "Downloading Minecraft's server.jar..."
    SERVER_MANIFEST_URL="$(curl "https://piston-meta.mojang.com/mc/game/version_manifest.json" | jq -r ".versions[] | select(.id == \"${SERVER_VERSION}\") | .url")"
    SERVER_JAR_DL="$(curl "$SERVER_MANIFEST_URL" | jq -r ".downloads.server.url")"
    curl --show-error --fail --location -o "${JAR_PATH}" "${SERVER_JAR_DL}"
fi

if [[ ! -d "${META_INF_PATH}" ]]; then
    echo "Extracting resources from Minecraft's server.jar..."
    (cd "${BUILD_DIR}" && "${GRAALVM_HOME}/bin/jar" xf "${JAR_PATH}" META-INF)
fi

if [[ ! -f "${META_INF_PATH}/classpath-joined" ]]; then
    # Some server distributions don't ship META-INF/classpath-joined.
    # Reconstruct a usable classpath from META-INF/libraries.list instead.
    if [[ ! -f "${LIBRARIES_LIST}" ]]; then
        echo "Unable to determine classpath (missing classpath-joined and libraries.list). Exiting..."
        exit 1
    fi

    echo "Reconstructing classpath from META-INF/libraries.list..."
    CLASSPATH_JOINED=""
    while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        # Format: <sha256>  *  <jarName>
        jar_name="$(echo "${line}" | awk '{print $3}')"
        [[ -z "${jar_name}" ]] && continue
        jar_path="${LIBRARIES_DIR}/${jar_name}"
        if [[ -f "${jar_path}" ]]; then
            if [[ -z "${CLASSPATH_JOINED}" ]]; then
                CLASSPATH_JOINED="${jar_path}"
            else
                CLASSPATH_JOINED="${CLASSPATH_JOINED};${jar_path}"
            fi
        fi
    done < "${LIBRARIES_LIST}"

    # Add the server jar itself at the end
    CLASSPATH_JOINED="${CLASSPATH_JOINED};${JAR_PATH}"
else
    CLASSPATH_JOINED=$(cat "${META_INF_PATH}/classpath-joined")
fi
# note: may be extended later (e.g. Paperclip patched jar), so don't mark readonly here

# Prefer the jar manifest Main-Class; META-INF/main-class can point to a class
# that only exists after Paperclip patches/extraction.
MAIN_CLASS="$(unzip -p "${JAR_PATH}" META-INF/MANIFEST.MF | tr -d '\r' | awk -F': ' '/^Main-Class:/{print $2; exit}')"
if [[ -z "${MAIN_CLASS:-}" && -f "${META_INF_PATH}/main-class" ]]; then
    MAIN_CLASS=$(cat "${META_INF_PATH}/main-class")
fi

SELFMAIN_BUILD_DIR="${BUILD_DIR}/selfmain"
SELFMAIN_CLASS="SelfMain"
rm -rf "${SELFMAIN_BUILD_DIR}"
mkdir -p "${SELFMAIN_BUILD_DIR}"

if [[ -z "${MAIN_CLASS:-}" ]]; then
    echo "Unable to determine main class. Exiting..."
    exit 1
fi

pushd "${META_INF_PATH}" > /dev/null

# Paperclip runtime patching expects its working dir to contain META-INF/* (download-context, patches, etc)
# and it loads CraftBukkit main via reflection. Ensure patched jar is present on classpath at runtime by:
# 1) running paperclip once to materialize the patched mojang server jar into META-INF/mojang_*.jar
# 2) compiling native-image with that jar on the classpath (so org.bukkit.craftbukkit.Main is included)
#
# We do this step only when the current server.jar is a paperclip bootstrap.
if unzip -p "${JAR_PATH}" META-INF/MANIFEST.MF 2>/dev/null | grep -qE '^Main-Class: io\.papermc\.paperclip\.Main'; then
    echo "Detected Paperclip server jar."

    # Prefer an already-materialized patched jar to avoid hanging on repeated Paperclip bootstrap runs.
    PATCHED_JAR="$(ls -1 "${META_INF_PATH}"/versions/*/spigot-*.jar 2>/dev/null | head -n 1 || true)"
    if [[ -n "${PATCHED_JAR}" ]]; then
        echo "Found existing patched jar, skipping Paperclip materialization: ${PATCHED_JAR}"
    else
        echo "Materializing patched mojang jar before native-image..."
        echo "Materializing patched jar (timeout: ${MATERIALIZE_TIMEOUT_SECONDS}s)..."
        run_with_optional_timeout "${MATERIALIZE_TIMEOUT_SECONDS}" "${GRAALVM_HOME}/bin/java" -jar "${JAR_PATH}" --version >/dev/null 2>&1 || true

        PATCHED_JAR="$(ls -1 "${META_INF_PATH}"/versions/*/spigot-*.jar 2>/dev/null | head -n 1 || true)"
    fi

    # Paperclip stores the patched jar under META-INF/versions/* (sometimes also creates mojang_*.jar).
    if [[ -z "${PATCHED_JAR}" ]]; then
        PATCHED_JAR="$(ls -1 "${META_INF_PATH}"/versions/*/*.jar 2>/dev/null | head -n 1 || true)"
    fi
    if [[ -z "${PATCHED_JAR}" ]]; then
        PATCHED_JAR="$(ls -1 "${META_INF_PATH}"/versions/*.jar 2>/dev/null | head -n 1 || true)"
    fi
    if [[ -z "${PATCHED_JAR}" ]]; then
        echo "Paperclip did not produce a patched jar under ${META_INF_PATH} (checked mojang_*.jar and versions/). Exiting..."
        exit 1
    fi

    # Patch CraftBukkit Main to remove the "Server will start in 20 seconds" delay in outdated builds.
    # Patch inside the jar (no duplicate entries) and verify.
    PATCH_WORK="${BUILD_DIR}/patch"
    mkdir -p "${PATCH_WORK}"
    if [[ -f "${PATCHED_JAR}" ]]; then
        cp -f "${SCRIPT_DIR}/work/patch_sleep.py" "${PATCH_WORK}/patch_sleep.py"
        echo "Patching CraftBukkit Main.class inside patched jar to remove outdated-build sleep..."
        if (cd "${PATCH_WORK}" && run_with_optional_timeout "${PATCH_TIMEOUT_SECONDS}" python3 patch_sleep.py "${PATCHED_JAR}" --in-jar); then
            echo "Verifying patched jar contains sleep delay constant patched to 0L..."
            (cd "${PATCH_WORK}" && run_with_optional_timeout "${PATCH_TIMEOUT_SECONDS}" python3 patch_sleep.py "${PATCHED_JAR}" --in-jar --verify-only)
        else
            echo "Warning: could not patch patched jar, skipping jar update."
        fi
    fi
    echo "Using patched jar on classpath: ${PATCHED_JAR}"

    echo "Compiling SelfMain.java..."
    "${GRAALVM_HOME}/bin/javac" -cp "${PATCHED_JAR}:${CLASSPATH_JOINED//;/:}" -d "${SELFMAIN_BUILD_DIR}" "${SCRIPT_DIR}/work/SelfMain.java"

    CLASSPATH_JOINED="${SELFMAIN_BUILD_DIR};${PATCHED_JAR};${CLASSPATH_JOINED}"
    MAIN_CLASS="${SELFMAIN_CLASS}"
    export CLASSPATH_JOINED
    echo "Using SelfMain for native image: ${MAIN_CLASS}"

    # Ensure runtime can resolve resource:/assets and resource:/data by having patched jar
    # embedded as a resource in the native image (only when jar is under BUILD_DIR).
    PATCHED_JAR_REL=""
    if [[ -n "${PATCHED_JAR}" ]]; then
        build_abs="$(cd "${BUILD_DIR}" && pwd)"
        patched_abs="$(cd "$(dirname "${PATCHED_JAR}")" && pwd)/$(basename "${PATCHED_JAR}")"
        case "${patched_abs}" in
            "${build_abs}"/*)
                PATCHED_JAR_REL="${patched_abs#${build_abs}/}"
                echo "Patched jar relative path (for resources): ${PATCHED_JAR_REL}"
                ;;
            *)
                echo "Patched jar is outside BUILD_DIR, skipping IncludeResources embedding: ${PATCHED_JAR}"
                ;;
        esac
    fi
fi

JNA_TMPDIR="${META_INF_PATH}/.jna"
mkdir -p "${JNA_TMPDIR}"
export TMPDIR="${JNA_TMPDIR}"
export JNA_TMPDIR="${JNA_TMPDIR}"
export JNA_NOSYS=true

EXTRA_NI_ARGS=()
if [[ -n "${PATCHED_JAR_REL:-}" ]]; then
    EXTRA_NI_ARGS+=( "-H:IncludeResources=\\Q${PATCHED_JAR_REL}\\E" )
fi

readonly MAIN_CLASS

NI_CMD=(
    "${NI_EXEC}" -H:+UnlockExperimentalVMOptions --no-fallback
    -H:ConfigurationFileDirectories="${AGENT_CONFIG_DIR}"
    -H:IncludeResources="\\Qjoptsimple/HelpFormatterMessages.properties\\E"
    -H:IncludeResources="\\Qjoptsimple/ExceptionMessages.properties\\E"
    -H:+AddAllCharsets
    -H:+ReportExceptionStackTraces
    --enable-url-protocols=https
    --add-modules=java.desktop
    --initialize-at-run-time=io.netty
    --enable-monitoring=heapdump,jfr
    --enable-native-access=ALL-UNNAMED
    -H:+SharedArenaSupport
    --initialize-at-build-time=net.minecraft.util.profiling.jfr.event
    --initialize-at-run-time=org.apache.logging.log4j
    --initialize-at-run-time=joptsimple
    --initialize-at-run-time=java.awt
    --initialize-at-run-time=javax.swing
    --initialize-at-run-time=sun.awt
    --initialize-at-run-time=org.apache.logging.log4j.core.util.DefaultShutdownCallbackRegistry
    -H:Name="${BINARY_NAME}"
    -cp "${CLASSPATH_JOINED//;/:}"
)

if [[ ${#EXTRA_NI_ARGS[@]} -gt 0 ]]; then
    NI_CMD+=( "${EXTRA_NI_ARGS[@]}" )
fi

if [[ $# -gt 0 ]]; then
    NI_CMD+=( "$@" )
fi

NI_CMD+=( "${MAIN_CLASS}" )

"${NI_CMD[@]}"


popd > /dev/null # Exit $META_INF_PATH
popd > /dev/null # Exit $BUILD_DIR

# if command -v upx &> /dev/null; then
#     echo "Compressing the native Minecraft server with upx..."
#     upx "${SCRIPT_DIR}/${BINARY_NAME}"
# fi

echo ""
echo "Done! The native Minecraft server is located at:"
echo "${SCRIPT_DIR}/${BINARY_NAME}"
