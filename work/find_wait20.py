import zipfile

JAR = "build/META-INF/versions/1.21.11/spigot-1.21.11.jar"

NEEDLES = [
    b"Server will start in 20 seconds",
    b"20 seconds",
    b"outdated-spigot",
    b"Please download a new build",
]

def main() -> None:
    with zipfile.ZipFile(JAR, "r") as z:
        for needle in NEEDLES:
            found = None
            for name in z.namelist():
                if not name.endswith(".class"):
                    continue
                data = z.read(name)
                if needle in data:
                    found = name
                    break
            if found:
                print(f"FOUND needle={needle!r} in {found}")
            else:
                print(f"NOT_FOUND needle={needle!r}")

if __name__ == "__main__":
    main()