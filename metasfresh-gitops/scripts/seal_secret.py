"""Seal local private input files directly through kubeseal; no plaintext temp file."""
import argparse
import base64
import json
import re
import subprocess
from pathlib import Path

def secret_manifest(namespace, name, secret_type, files, registry=None,
                    username_file=None, password_file=None):
    for value in (namespace, name):
        if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", value) or len(value) > 63:
            raise ValueError("Invalid Kubernetes namespace/name")
    data = {}
    for key, path in files.items():
        if not re.fullmatch(r"[-._a-zA-Z0-9]+", key):
            raise ValueError("Invalid Secret key")
        payload = Path(path).read_bytes()
        if not payload:
            raise ValueError("Empty input file for key " + key)
        data[key] = base64.b64encode(payload).decode("ascii")
    if registry:
        if files or not username_file or not password_file or "/" in registry:
            raise ValueError("Registry mode needs only registry, username-file and password-file")
        username = Path(username_file).read_text(encoding="utf-8").rstrip("\r\n")
        password = Path(password_file).read_text(encoding="utf-8").rstrip("\r\n")
        if not username or not password or "\n" in username + password or "\r" in username + password:
            raise ValueError("Invalid registry credentials")
        auth = base64.b64encode((username + ":" + password).encode()).decode()
        config = {"auths": {registry: {"username": username, "password": password, "auth": auth}}}
        data[".dockerconfigjson"] = base64.b64encode(json.dumps(config).encode()).decode()
        secret_type = "kubernetes.io/dockerconfigjson"
    if not data:
        raise ValueError("At least one non-empty private file is required")
    if secret_type == "kubernetes.io/tls" and set(data) != {"tls.crt", "tls.key"}:
        raise ValueError("TLS Secret requires tls.crt and tls.key files")
    return {"apiVersion": "v1", "kind": "Secret", "type": secret_type,
            "metadata": {"namespace": namespace, "name": name}, "data": data}

def seal(secret, cert):
    command = ["kubeseal", "--cert", str(cert), "--scope", "strict", "--format", "json"]
    result = subprocess.run(command, input=json.dumps(secret).encode(), capture_output=True, check=False)
    if result.returncode:
        # Never echo stderr from a credential-bearing child process.
        raise ValueError("kubeseal failed; check certificate, tool version and input files")
    sealed = json.loads(result.stdout)
    if sealed.get("kind") != "SealedSecret" or sealed.get("spec", {}).get("template", {}).get("data"):
        raise ValueError("Output is not an encrypted SealedSecret")
    if sealed.get("spec", {}).get("template", {}).get("stringData"):
        raise ValueError("Plaintext stringData in sealed output")
    if set(sealed.get("spec", {}).get("encryptedData", {})) != set(secret["data"]):
        raise ValueError("Sealed output keys differ from requested Secret")
    if any(not value for value in sealed["spec"]["encryptedData"].values()):
        raise ValueError("Empty encrypted value")
    return sealed

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--type", default="Opaque")
    parser.add_argument("--file", action="append", default=[], metavar="KEY=PRIVATE_FILE")
    parser.add_argument("--registry")
    parser.add_argument("--username-file", type=Path)
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--cert", required=True, type=Path, help="Public Sealed Secrets controller certificate")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        files = {}
        for item in args.file:
            key, separator, path = item.partition("=")
            if not separator or not path or key in files:
                raise ValueError("Use unique --file KEY=PRIVATE_FILE")
            files[key] = Path(path)
        private_inputs = {p.resolve() for p in files.values()}
        private_inputs.update(p.resolve() for p in (args.username_file, args.password_file) if p)
        if args.output.resolve() in private_inputs:
            raise ValueError("Output must not overwrite a private input")
        if args.output.exists():
            raise ValueError("Output already exists; review/move it before resealing")
        secret = secret_manifest(args.namespace, args.name, args.type, files, args.registry,
                                 args.username_file, args.password_file)
        result = seal(secret, args.cert)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation avoids clobbering a concurrent rotation.
        with args.output.open("x", encoding="utf-8") as target:
            json.dump(result, target, indent=2)
            target.write("\n")
        print("Wrote encrypted SealedSecret: " + str(args.output))
        return 0
    except (OSError, ValueError) as error:
        print("Sealing failed: " + str(error))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
