# Security Policy

otaku can store your stories **encrypted at rest** (AES-256-GCM, sealed client-side before
anything touches disk) and is built for people who run models locally because they care about
privacy. Security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Use GitHub's private vulnerability reporting instead — **Security → Report a vulnerability** on
the repository: <https://github.com/enclavum/otaku/security/advisories/new>

You'll get an acknowledgement, and any fix or mitigation will be coordinated before public
disclosure.

## Threat model

Encryption is opt-in (`[encryption]` in `config.toml`; the default stores plain text). With a
provider configured, the at-rest encryption is designed to protect your stories against:

- someone reading the database or backup files — disk images, Time Machine, cloud-synced or
  copied files;
- other applications or users on the machine reading your stories off disk.

How well the key itself is protected depends on the provider you choose: `keychain` keeps it in
the OS keychain, `command` delegates to an external custodian (a password manager, a
hardware-token tool), `passphrase` derives it and stores nothing, and `disk` (the zero-friction
opt-out) leaves it on the same disk at mode `0600`.

**On Windows, two of those are weaker.** otaku looks for a keychain by asking for macOS's
`security` or Linux's `secret-tool`, and finds neither, so `keychain` is unavailable and a key
otaku stores itself lands on disk instead — Windows' own credential store (DPAPI, Credential
Manager) is not yet used. And the `0600` and `0700` modes otaku sets are POSIX permissions, which
Windows does not enforce: the key files and the directory holding them are readable by other
accounts on the machine. If that matters to you on Windows, use `passphrase` (which stores
nothing) or `command` (which hands custody to a tool you trust), and keep the state dir on a
volume only you can reach.

It does **not** protect against:

- an attacker who already has your logged-in user account and can run your key provider;
- inspection of a running process's memory;
- a compromised or malicious model server / network endpoint you've configured — story content
  is sent to the model you point otaku at.

The request log seals its entries with the same cipher; with encryption off, it is plain
text too: every prompt as sent, kept indefinitely — deleting a story does not delete its wire
history. The log directory is yours to prune.

## The web interface

`otaku web` serves one open session to a browser. By default it listens on loopback, port 9600,
over plain HTTP, and asks for no password: whoever can reach the port has the session — every
story, the ability to play, and the provider fields. On loopback that means programs on this
machine, and that is the design.

Reaching it from another machine is a decision made in `configs/config.toml`'s `[web]` section,
and two settings beside `host` exist for it.

- **`https = true`** serves over TLS. otaku generates a self-signed certificate into the state
  dir's `cert/` on first use and never writes over it after, so a certificate of your own
  (mkcert, `tailscale cert`) simply goes in its place. A self-signed certificate encrypts the
  connection but proves nothing about who is on the other end: every browser warns once, and
  clicking through a warning you did not expect accepts an impersonator as readily as otaku.
- **`password`** makes the page ask for one. The right password earns a signed token in an
  `HttpOnly`, `SameSite=Strict` cookie — kept 30 days when "stay signed in" is ticked, 4 hours
  when it is not. Every API request without a good token is refused (401); the
  page itself loads without one.

Neither protects against the following:

- **Without `https`, everything crosses the network readable** — the password at sign-in, the
  cookie on every request after it, and every story. A captured cookie is a working credential
  until it expires.
- **Signing out revokes nothing.** It clears this browser's cookie. The server keeps no sessions,
  so a copied token stays good until its expiry; changing the password is what invalidates every
  token at once.
- **Guessing is slowed, not stopped.** The password is stored as a salted scrypt hash, which
  limits guessing to a couple of dozen attempts a second — a short or common password will not
  survive that. Anyone who can read `config.toml` can guess offline against the hash, and can
  sign tokens without guessing at all.
- **Browsers scope cookies by host, not by port.** Every other web app served from the same
  host receives the cookie too.

On a network you do not control, set both, and prefer a certificate your browser already trusts.

Loopback is not the whole defence either, because the browser that reads the page also reads
everything else. Two guards sit in front of every request:

- **DNS rebinding.** A name that resolves to 127.0.0.1 lets a page you are reading address your
  otaku. A request whose `Host` is not this machine is refused (421) — except under a wildcard
  bind, where answering to every name is the whole point.
- **Cross-origin writes.** A form on another site can POST without a preflight, and a write does
  its damage without ever reading the answer. Every write must carry `Sec-Fetch-Site: same-origin`
  or an `Origin` equal to the one it was addressed to; anything else is refused (403). A request
  with neither header is not a browser (curl, a script on this machine) and is let through — the
  bind is what guards those. A read is not asked: another page can cause one but cannot see the
  answer, and a read changes nothing.

What may be served is a closed table of files, so no request can compose its way to
`configs/providers.toml`, and the page is never sent an api key's value — only whether one is set.

## Provider API keys

Cloud provider API keys never sit in a config file as plain text. They are stored in
`configs/providers.toml` sealed (AES-256-GCM) — a plane fully separate from the story encryption
above, with its own key, and independent of `[encryption]` being enabled at all. The sealing key
lives in the OS keychain (macOS `security`, Linux `secret-tool`), one item per state dir; on a
machine without a keychain tool — every Windows machine, as above — it falls back to
`configs/config.key` at mode `0600`, which Windows does not enforce. A key pasted into the file
as plain text is sealed automatically at the next launch, and the dated backup that edit leaves
in `configs/backups/` has the key replaced with `[REDACTED]`.

Leaking `providers.toml` alone therefore leaks no credentials. The same limits as above apply:
an attacker running as your logged-in user can read the keychain item, and a running process's
memory holds the open keys.
