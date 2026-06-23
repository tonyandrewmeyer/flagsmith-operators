# Security Policy

## Supported versions

Security fixes are applied to the latest released revision of each charm on
its tracked Charmhub channel. We recommend always running the most recent
revision.

## Reporting a vulnerability

**Do not open a public issue for security vulnerabilities.**

Please report suspected vulnerabilities in these charms privately via GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Report a vulnerability" on the Security tab), or by email to the maintainers.

Include:

- the charm and revision affected;
- a description of the issue and its impact;
- reproduction steps or a proof of concept.

We aim to acknowledge reports within three business days and to provide a
remediation timeline after triage.

### Vulnerabilities in Flagsmith itself

These charms package the upstream Flagsmith application but are not the
application. Vulnerabilities in the Flagsmith API, frontend or edge proxy
should be reported to the upstream project following its
[security policy](https://github.com/Flagsmith/flagsmith/security/policy).

## Security model notes

- The charms never persist database or cache credentials to charm config or
  logs; credentials are exchanged over Juju relations and Juju secrets and
  injected into the workload via the Pebble environment.
- The Django `secret-key` is supplied as a Juju user secret granted to the
  application, never as a plaintext config option.
- Enable TLS termination at ingress (`traefik-k8s` with a `tls-certificates`
  provider) for any production deployment.
