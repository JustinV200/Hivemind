# hivemind.entrance.enrol

Every client of the Hive Entrance is a device enrolled with its own key and approved at the Hive
Stand (codingrules 8.15, ADR-0033). This package is that whole lifecycle (roadmap 10.5d): the
device record and its state machine, the Hive Stand console's bootstrap, and the flows that move a
device through the machine, each one recorded on the Pheromone Trail as a `guard.entrance_*` event
in the same step as the change, pushed to every other device, and followed by cutting the device
off whenever it leaves its approval.

## The lifecycle

```text
 hive entrance invite            device presents code + key          operator on loopback
 ───────────────────►  INVITED  ─────────────────────────►  PENDING  ───────────────────►  APPROVED ◄──► LOCKED
   mint_invite            │        redeem_ed25519 /            │      approve (ceiling,      │  lock / unlock │
   (code, link, QR)       │        redeem_passkey              │      cap, expiry, mode)     │                │
                          ├─► REVOKED  cancel_invite           ├─► DENIED   deny           ├─► REVOKED  revoke (goals)
                          └─► EXPIRED  expire_due              └─► EXPIRED  expire_due     └─► EXPIRED  expire_due
```

`state.py` holds the one table (`TRANSITIONS`) with the trail kind of every edge
(`guard.entrance_<new status>`, `guard.entrance_unlocked` for LOCKED to APPROVED) and of the two
entries (`ENTRY_TRAIL_KINDS`: `guard.entrance_invited`, and `guard.entrance_approved` for the
console). DENIED, EXPIRED and REVOKED are terminal.

## Modules

| Module | What it does |
|---|---|
| `state.py` | `DeviceStatus`, the transition table, the entry kinds. |
| `models.py` | `EnrolledDevice`, `DeviceDescription` (display text only), `DeviceInvite` (stored as its code's SHA-256), `OperatorCredential`. |
| `console.py` | `bootstrap_operator`, `change_operator_password`, `unlock_console_key`: the loopback-bound console, its key wrapped under the password, its entry recorded as approved. |
| `deps/` | `EnrolmentDeps` (four bundles: records, rules, ceremony, seams), `EntranceIdentity` (who events are recorded as, and the one place they are built), and the three seams with their no-ops and recording fakes. |
| `record.py` | `apply_transition`: the edge with its event, then offboarding when the device left an approval, then the notice. Payload helpers keep events bounded. |
| `invite.py` | `mint_invite` (128 random bits as grouped base32, the link `<base>/enrol#code=<code>`, a terminal and an SVG QR code), `cancel_invite`, and the code's canonical form and hash. |
| `redeem.py` | `passkey_options`, `redeem_ed25519`, `redeem_passkey`: INVITED to PENDING, the invite spent in the same atomic step; every refusal is one `EnrolmentRefusedError` and a `guard.entrance_redeem_failed` event naming the address and a reason category. |
| `decisions.py` | `ApprovalRequest`, `approve` (name, capabilities within the `device` ceiling or the proposed set, daily spend cap, expiry, interactivity), `deny`. |
| `standing.py` | `revoke` (refuses the device's goal requests not planned yet, lists its open goals, cancels them when asked, names those left running), `lock`, `unlock`, `expire_due`. The console is never revoked or expired here. |
| `grants.py` | The pure rules: `approval_grant` (the device ceiling), `steward_grant` (a steward's own set within the ceiling, never `entrance:steward`). |

## The seams later steps implement

| Seam | Default | Implemented by |
|---|---|---|
| `SecurityNotifier.notify(notice)`: "something happened to device X", the trail event as the push's `ref` | `NullSecurityNotifier` | roadmap 10.5b (push) |
| `DeviceOffboarder.offboard(device_id, reason)`: end sessions, delete push subscriptions (idempotent) | `NullDeviceOffboarder` | roadmap 10.5e (sessions: `hivemind.entrance.auth.SessionBook.offboard`) and 10.5b (subscriptions: `PushDispatcher.forget_device`); the app composes the two |
| `GoalLedger.open_goals(device_id)`, `cancel_goals(goal_ids, reason)`, `refuse_requests(device_id, reason)` | `NullGoalLedger` | the Queen's door (`hivemind.entrance.runtime.QueenGoalLedger`): her goal-request table, and a cancelled goal's placed work stopped on its Warden (`TaskCancel`) |

Each no-op is safe on its own: the trail records every event whether or not anyone is told, and
no device can hold a session, a subscription or a goal before the step that implements its seam.

## What never leaves

The invite code exists only in the `MintedInvite` returned once (its `repr` hides it); the tables
hold its SHA-256 over the grouped form, which is also what a program signs. No trail event, log
line or error message carries a code, a key, a signature or a password, and a redemption refusal
never says which check failed. Approval, denial, unlock, invite minting and revocation are
loopback-only decisions: the routes that call these functions (later steps) enforce that.

## Public API

The state machine (`DeviceStatus`, `TRANSITIONS`, `TERMINAL_STATUSES`, `ENTRY_TRAIL_KINDS`,
`INVITED_TRAIL_KIND`, `APPROVED_TRAIL_KIND`, `assert_transition`, `can_transition`, `trail_kind`,
`is_terminal`); the records; the console (`ConsoleDeps`, `bootstrap_operator`,
`change_operator_password`, `unlock_console_key` and its constants); the dependencies
(`EnrolmentDeps`, `EnrolmentRecords`, `EnrolmentRules`, `EnrolmentCeremony`, `EnrolmentSeams`,
`EntranceIdentity`) and seams (`SecurityNotice`, `SecurityNotifier`, `DeviceOffboarder`,
`GoalLedger`, their `Null*` no-ops and recording fakes); invites (`MintedInvite`, `InviteQr`,
`mint_invite`, `cancel_invite`, `new_invite_code`, `canonical_invite_code`, `invite_code_hash`,
`invite_url`, `INVITE_PATH`); redemption (`Ed25519Proof`, `Redemption`, `RedeemFailure`,
`RedeemStep`, `passkey_options`, `redeem_ed25519`, `redeem_passkey`, `ENROLMENT_CHALLENGE_TTL`,
`REDEEM_FAILED_KIND`); decisions (`ApprovalRequest`, `approve`, `deny`); standing (`LockReason`,
`Revocation`, `revoke`, `lock`, `unlock`, `expire_due`); grants (`approval_grant`,
`device_ceiling`, `steward_grant`); and `OPERATOR_ACTOR`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance/enrol \
    packages/hivemind/tests/contracts/test_entrance_store_contract.py
```

`test_state.py` walks every allowed edge and asserts every forbidden one raises; the store's
contract suite does the same through both stores, with each edge's event. `test_redeem.py` covers
every refusal path and its trail event, and races two redemptions of one code with
`asyncio.gather` over both stores (exactly one wins). `test_flow.py` runs a whole lifecycle for an
Ed25519 program and a passkey browser (`SoftPasskey` through the real `webauthn` verification)
over in-memory tables and one real SQLite file, then checks that no trail event carries a code, a
key, a signature, a credential or the password. Builders, including the `Enrolment` rig, live in
`packages/hivemind/tests/builders/entrance.py`.
