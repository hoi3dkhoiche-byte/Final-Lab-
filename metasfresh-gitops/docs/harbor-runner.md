# Harbor CA and self-hosted runner

Actual registry: https://10.60.50.30, Harbor 2.15.2, internal CA; certificate SAN
must include IP 10.60.50.30. Verify with the PUBLIC CA from /opt/harbor/pki/ca.crt:

```bash
curl --cacert harbor-ca.crt https://10.60.50.30/api/v2.0/ping
```

The response must be Pong with a valid certificate. Create private project
metasfresh, one build/push robot, and a different pull-only robot. Configure
retention so deployed digests remain available and scan the published images.

## Runner at Harbor VM or a dedicated ADMIN VM

Register a Linux runner with labels self-hosted,linux,cmc-lab only after
confirming the actual VM/address. The plan records co-location on Harbor
10.60.50.30; installation/registration has not been verified. Install Docker
with buildx, Bash, Python3 + PyYAML6.0.2, Trivy, Helm, gh, AWS CLI and PostgreSQL18
psql/pg_dump/pg_restore. The runner only requires outbound GitHub access.

Install the CA under /etc/docker/certs.d/10.60.50.30/ca.crt and in the OS trust
store using the distribution's supported update mechanism. Restart Docker in a
maintenance window. Verify docker login and digest image pulls. Do not use
insecure-registries or skip TLS checks. Private key /opt/harbor/pki/harbor.key
stays on the Harbor host.

The runner needs private RDS5432 and S3 HTTPS reachability; the build additionally
needs source/package registries. Allow endpoints documented by the providers,
including required redirected package/CDN destinations. Restrict ingress to
Bastion administration; opening inbound ports does not register a runner.

## CMC CaaS workers

Image pulls originate from node runtime, not the application pod. Ask CMC for the
supported private-CA registry procedure for the actual container runtime and
worker image. Configure EVERY APP/MON/PLATFORM worker and future replacement or
autoscaled workers. A namespace imagePullSecret authenticates but does not add
a CA to containerd.

For containerd with a supported registry config_path, the host configuration
has this shape; the provider must confirm the config_path/version and roll-out:

```toml
server = "https://10.60.50.30"

[host."https://10.60.50.30"]
  capabilities = ["pull", "resolve"]
  ca = "/etc/containerd/certs.d/10.60.50.30/ca.crt"
```

containerd1 uses plugins."io.containerd.grpc.v1.cri".registry.config_path;
containerd2 uses plugins."io.containerd.cri.v1.images".registry.config_path.
Do not blindly overwrite config.toml, restart all workers, or bypass TLS.
Prefer the CMC-supported node configuration/template so replacements inherit
the setting. Verify actual registry communication after the change.

Run a short-lived, token-free private-image pull test on each worker using
harbor-pull and an existing tested digest. Confirm imageID/Ready and remove the
test pods. Only then set harbor.workerRuntimeTrustVerified=true in site.yaml.

Sources: [Docker registry certificates](https://docs.docker.com/engine/security/certificates/),
[containerd registry hosts](https://github.com/containerd/containerd/blob/main/docs/hosts.md).
