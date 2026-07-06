# Security Policy

otaku stores conversation history **encrypted at rest** (AES-256-GCM, encrypted
client-side before it touches disk) and is built for people who run models
locally because they care about privacy. Security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Use GitHub's private vulnerability reporting instead — **Security → Report a
vulnerability** on the repository:
<https://github.com/enclavum/otaku/security/advisories/new>

You'll get an acknowledgement, and any fix or mitigation will be coordinated
before public disclosure.

## Threat model

The at-rest encryption is designed to protect your history against:

- someone reading the database or backup files — disk images, Time Machine,
  cloud-synced or copied files;
- other applications or users on the machine reading your conversations off disk.

It does **not** protect against:

- an attacker who already has your logged-in user account — the encryption key
  currently lives on the same disk (mode `0600`);
- inspection of a running process's memory;
- a compromised or malicious model server / network endpoint you've configured.

Stronger key custody (Secure Enclave / Touch ID, key-wrapping so the key need not
sit beside the data) is on the roadmap.
