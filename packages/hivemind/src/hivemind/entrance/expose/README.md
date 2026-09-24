# hivemind.entrance.expose

How the Hive Entrance is reached from other machines, and nothing more (ADR-0033, codingrules
8.15). The loopback listener, where approval lives, is always on; `[entrance] expose` may add a
remote listener, and only in one of three shapes. There is no public mode: the schema has no such
value, and every remote mode is TLS on a DNS name.

| `expose` | Remote listener | Also required |
|---|---|---|
| `loopback` (default) | none | nothing |
| `vpn` (recommended) | `remote_bind`: a specific, not globally routable address inside `vpn_cidrs` **and** assigned to the overlay's own interface (`vpn_interface`; empty means `tailscale0` on Linux, `Tailscale` on Windows; macOS names its `utun`) | TLS; mutual TLS when `mutual_tls` (the default) |
| `lan` | `remote_bind`: a private-network address this host has (RFC 1918, link-local, or an IPv6 unique local address; never a global one or carrier-grade NAT space) | TLS and mutual TLS |
| `tunnel` | `remote_bind` on loopback (another port than `bind`), reached by the supervised `tunnel_command` | TLS and mutual TLS |

"TLS" means `public_url` names a DNS host (never an IP, never `localhost`), the `[entrance.tls]`
certificate and key are readable, unencrypted PEM, belong together, are valid now, and the
certificate's names cover that host; `rp_id`, when set, is that host or a parent domain of it.

```text
                      [entrance] section ──┐
 SystemInterfaces (psutil) ─┐              ▼
 [entrance.tls] files ──────┼─► gather_facts ─► ExposureFacts ─► plan_exposure ─► ExposurePlan
 platform, clock ───────────┘                                        │ or ExposureRefusedError(rule)
                                                                     ▼
   loopback listener ◄── plan.loopback     remote listener ◄── server_context(plan.remote.tls,
   (loopback_request_allowed on every          │                 HiveAuthority, build_crl(...))
    request: loopback Host, no proxy)          └── ContextSwitch: sni_callback; rebuild on revocation
                                           tunnel mode ◄── TunnelSupervisor(plan.tunnel_argv,
                                                              tunnel_environment(...), clock)
```

## Layout

| Module | What it holds |
|---|---|
| `rules.py` | `ExposureRule`: every refusal, a stable code and one sentence each. |
| `errors.py` | `ExposeError` and its refusals: `ExposureRefusedError` (names the rule), `CertificateAuthorityError`, `CertificateRequestError`, `CertificateIssueError`. |
| `facts.py` | `ExposureFacts`, `TlsFacts`, `FileState`, `HostPlatform`: the frozen host picture the check reads. |
| `gather.py` | `gather_facts`, `read_tls_facts`: the adapter that reads the host (in a worker thread). |
| `plan.py` | `plan_exposure`, `ExposurePlan`, `ListenerPlan`, `ListenerTls`: the pure mode check. |
| `names.py` | `public_host`, `public_origin`, `certificate_covers`, `is_same_or_parent_domain`: DNS name rules. |
| `loopback.py` | `loopback_request_allowed`: the loopback listener's `Host` and forwarding-header check. |
| `tunnel.py` | `TunnelSupervisor`, `tunnel_environment`: the one Entrance module that starts a process. |
| `process_tree.py` | `kill_process_tree`: ends a child and everything it started (Windows has no process groups). |
| `interfaces/` | `LocalInterfaces`, `SystemInterfaces` (psutil), `FakeInterfaces`. |
| `tls/` | The Hive's certificate authority (`entrance.ca_key`, `entrance.ca_cert` in the secret store), device certificates (from a CSR, or PKCS#12 for a browser), the revocation list, `server_context`, `ContextSwitch`. |

## Decisions worth knowing

- **Mutual TLS is TLS 1.3 only, with no session tickets.** A TLS 1.2 client can resume a session
  by id, and a resumed handshake never re-checks the client certificate, so a device revoked since
  would get back in past the rebuilt list (a test demonstrates the refusal). Without mutual TLS the
  floor is TLS 1.2.
- **Revocation reaches the next handshake, not live connections.** `ContextSwitch` swaps a rebuilt
  context in through the listener context's `sni_callback`, which OpenSSL calls on every
  ClientHello, server name or not. A connection already established is ended by the login layer,
  which refuses a revoked device and closes its sessions and sockets in the revocation's own step.
- **A browser's PKCS#12 bundle holds its key and certificate, never the Hive's authority.** A
  phone installs a CA it finds in a bundle as a trusted root, and whoever held the authority's key
  (in the secret store, readable by the Hive's own OS user) could then impersonate any site to it.
  The server holds the authority; a device never needs it.
- **The revocation list never leaves the Hive Stand**, so its `nextUpdate` is the authority's own
  end: freshness comes from rebuilding it on every revocation and start. Python can load a list
  only from a file, so `server_context` writes the (public) authority certificate and list to a
  private temporary directory for the load and deletes it at once.
- **The tunnel child gets the Hive's environment minus every `HIVEMIND_*` variable, plus each
  `HIVEMIND_ENTRANCE_TUNNEL_<NAME>` as `<NAME>`.** No shell expands variables in an argv, so a
  client (cloudflared's `TUNNEL_TOKEN`, say) can only find its token under its own name; and a
  third-party client has no business with the Hive's provider or VAPID keys. Its stdio is the null
  device; logs name the program, a pid, an exit code and a delay, never arguments or environment.
  `stop()` sends SIGTERM (to the whole process group on POSIX), waits half a second, then SIGKILL:
  it returns within about a second, the Entrance Reducer's bound for cutting the door. Windows has
  no process-group signal, so there `process_tree.kill_process_tree` ends every process the child
  started, then the child, and no helper keeps the door open.
- **The loopback check is a deny-list plus a strict Host.** Proxies that announce themselves
  (`Forwarded`, `X-Forwarded-*`, `X-Real-IP`, `Via`, `Tailscale-*`) are refused; a bare TCP
  forwarder is invisible at HTTP level, which is why the client guide forbids fronting loopback.
- **The travel lock's own refusal** (`travel_lock` needs `vpn`) lives with the travel lock in
  `hivemind.entrance.auth.travel`, not here.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance/expose
```

`test_plan.py` plans every mode and `test_plan_refusals.py` raises every `ExposureRule` (a test
asserts the table covers them all), including phase 10's exit criterion that `expose = "lan"`
without mutual TLS refuses to start. The TLS tests run real handshakes between two `ssl.MemoryBIO` endpoints, so every verdict
(a revoked serial, an expired certificate, a stranger's authority, a TLS 1.2 client, a resumption
after revocation) is OpenSSL's own. The tunnel tests run the test interpreter itself as the child
on a FakeClock that reports each sleep, so backoff and the stop grace period never wait on a timer.
