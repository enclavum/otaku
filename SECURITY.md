# Security Policy

otaku can store your stories **encrypted at rest** (AES-256-GCM, sealed
client-side before anything touches disk) and is built for people who run
models locally because they care about privacy. Security reports are taken
seriously.

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Use GitHub's private vulnerability reporting instead — **Security → Report a
vulnerability** on the repository:
<https://github.com/enclavum/otaku/security/advisories/new>

You'll get an acknowledgement, and any fix or mitigation will be coordinated
before public disclosure.

## Threat model

Encryption is opt-in (`[encryption]` in `config.toml`; the default stores
plain text). With a provider configured, the at-rest encryption is designed
to protect your stories against:

- someone reading the database or backup files — disk images, Time Machine,
  cloud-synced or copied files;
- other applications or users on the machine reading your stories off disk.

How well the key itself is protected depends on the provider you choose:
`keychain` keeps it in the OS keychain, `command` delegates to an external
custodian (a password manager, a hardware-token tool), `passphrase` derives
it and stores nothing, and `disk` (the zero-friction opt-out) leaves it on
the same disk at mode `0600`.

It does **not** protect against:

- an attacker who already has your logged-in user account and can run your
  key provider;
- inspection of a running process's memory;
- a compromised or malicious model server / network endpoint you've
  configured — story content is sent to the model you point otaku at.

The request log seals its entries with the same cipher; the system and
error logs are content-free by contract (ids, counts, and tracebacks
without locals — never story text).
