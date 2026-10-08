"""Validate complete build evidence before updating any release file."""
import argparse
import json
import os
import re
from pathlib import Path
import yaml
from verify_build import verify


def named_template(text, name):
    """Return one named Go template, including its nested control blocks."""
    depth, start = 0, None
    for token in re.finditer(r"\{\{(.*?)\}\}", text, re.DOTALL):
        action = token.group(1).strip().strip("-").strip()
        if start is None:
            if re.fullmatch(r'define\s+"' + re.escape(name) + r'"', action):
                depth, start = 1, token.end()
            continue
        if re.match(r"^(?:define|block|if|range|with)\b", action):
            depth += 1
        elif action == "end":
            depth -= 1
            if depth == 0:
                return text[start:token.start()]
    return None


def renders_digest_image(text):
    """Require the digest branch on an actual image field, not a comment."""
    return bool(re.search(
        r"^\s*image:\s*[^\n]*\{\{-?\s*if\s+"
        r"(?P<digest>[\w.$]+\.image\.digest)\s*-?\}\}\s*@\s*"
        r"\{\{-?\s*(?P=digest)\s*-?\}\}",
        text, re.MULTILINE))


def digest_aware_deployment(root, component):
    template = root / f"charts/erp-adapter/templates/deployment-{component}.yaml"
    text = template.read_text(encoding="utf-8")
    if renders_digest_image(text):
        return True
    # The current chart delegates each complete Deployment to this helper.
    wrapper = (
        r'\{\{-?\s*include\s+"erp\.deployment"\s+\(dict\s+"root"\s+\.\s+'
        r'"component"\s+"' + re.escape(component) + r'"\s+"port"\s+\d+\s*\)\s*-?\}\}'
    )
    if not re.fullmatch(wrapper, text.strip()):
        return False
    helpers = root / "charts/erp-adapter/templates/_helpers.tpl"
    if not helpers.is_file():
        return False
    body = named_template(helpers.read_text(encoding="utf-8"), "erp.deployment")
    return body is not None and renders_digest_image(body)


def update(root, evidence_dir, build, registry, project, repository):
    source = verify(build, repository)
    root, evidence_dir = Path(root), Path(evidence_dir)
    evidence = json.loads((evidence_dir / "release.json").read_text(encoding="utf-8"))
    expected = {"GITHUB_SHA": source, "GITHUB_RUN_ID": str(build["id"]),
                "GITHUB_RUN_ATTEMPT": str(build["run_attempt"]),
                "HARBOR_REGISTRY": registry, "HARBOR_PROJECT": project}
    if any(evidence.get(k) != v for k, v in expected.items()):
        raise ValueError("Artifact provenance or registry configuration mismatch")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*(?::[0-9]+)?", registry):
        raise ValueError("Harbor registry must be a hostname or IP with optional port")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", project):
        raise ValueError("Invalid Harbor project")
    values_path = root / "charts/erp-adapter/values.yaml"
    lock_path = root / "versions.yaml"
    values = yaml.safe_load(values_path.read_text(encoding="utf-8"))
    lock = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    images = {}
    for component, image, key in [("core", "metas-app", "app"), ("api", "metas-api", "api"), ("ui", "metas-frontend", "frontend")]:
        digest = json.loads((evidence_dir / f"{image}.json").read_text(encoding="utf-8"))["digest"]
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise ValueError(f"Invalid digest for {image}")
        if not digest_aware_deployment(root, component):
            raise ValueError(f"Install digest-aware chart first: deployment-{component}.yaml")
        image_repo = f"{registry}/{project}/{image}"
        values[component]["image"].update(repository=image_repo, tag="", digest=digest)
        images[key] = f"{image_repo}@{digest}"
    # Write only after all evidence and all three template contracts pass.
    values.setdefault("global", {})["registry"] = registry
    lock.update(images=images, source_commit=source, pipeline_url=build["html_url"])
    values_path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    lock_path.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-run", required=True)
    args = parser.parse_args()
    update("gitops-release", "ci-output",
           json.loads(Path(args.build_run).read_text(encoding="utf-8")),
           os.environ["HARBOR_REGISTRY"], os.environ["HARBOR_PROJECT"], os.environ["GITHUB_REPOSITORY"])
