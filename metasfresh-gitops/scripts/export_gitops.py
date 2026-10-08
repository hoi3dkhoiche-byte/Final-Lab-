"""Copy the reviewed deployment bundle into the separate GitOps checkout. No push."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_config import ROOT, load_site

ALLOWED = ("charts", "platform", "environments", "scripts", "docs", "sealed-secrets", "policies/rbac")
TOP = ("README.md", "versions.yaml", ".gitignore")

def sources(root=ROOT):
    root = Path(root)
    result = []
    for part in ALLOWED:
        directory = root / part
        if directory.exists():
            result += [p for p in directory.rglob("*") if p.is_file()
                       and "__pycache__" not in p.parts and p.suffix != ".pyc"
                       and "private-inputs" not in p.parts]
    result += [root / name for name in TOP if (root / name).is_file()]
    # Only encrypted Kubernetes manifests are exportable from sealed-secrets.
    for path in result:
        if path.relative_to(root).parts[0] == "sealed-secrets" and path.suffix in (".json", ".yaml", ".yml"):
            import yaml
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("kind") != "SealedSecret":
                raise ValueError("Unsealed manifest refused: " + str(path.relative_to(root)))
            if not document.get("spec", {}).get("encryptedData"):
                raise ValueError("Empty sealed manifest refused: " + str(path.relative_to(root)))
            if document.get("spec", {}).get("template", {}).get("data") or document.get("spec", {}).get("template", {}).get("stringData"):
                raise ValueError("Plaintext data refused: " + str(path.relative_to(root)))
    return sorted(set(result))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, type=Path, help="Existing checkout of GitOps-final-lab")
    parser.add_argument("--copy", action="store_true", help="Write files; default lists them only")
    args = parser.parse_args()
    try:
        target = args.target.resolve()
        if target == ROOT.resolve() or ROOT.resolve() in target.parents:
            raise ValueError("Target must be a separate repository outside this bundle")
        remote = subprocess.run(["git", "-C", str(target), "remote", "get-url", "origin"],
                                capture_output=True, text=True, check=True).stdout.strip()
        expected = load_site()["gitops"]["repository"]
        if remote.removesuffix(".git").rstrip("/") != expected.removesuffix(".git").rstrip("/"):
            raise ValueError("Target origin differs from the configured GitOps repository")
        source_files = sources()
        for source in source_files:
            destination = target / source.relative_to(ROOT)
            if destination.exists() and destination.read_bytes() != source.read_bytes():
                raise ValueError("Existing different target file; review manually: " + str(destination))
        for source in source_files:
            relative = source.relative_to(ROOT)
            if args.copy:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    with destination.open("xb") as output:
                        output.write(source.read_bytes())
            print(relative.as_posix())
        print("Copied." if args.copy else "Preview only; pass --copy after reviewing the target checkout.")
        print("No commit or push was performed. Review the Git diff before publishing.")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print("Export blocked: " + str(error))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
