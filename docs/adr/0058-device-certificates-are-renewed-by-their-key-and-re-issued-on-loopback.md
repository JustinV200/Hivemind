# ADR-0058: Device certificates are renewed by their own key and re-issued on loopback

- Status: Accepted
- Date: 2026-10-02

## Context

ADR-0041 gives every device that reaches a mutual-TLS listener (`lan` and `tunnel` always, `vpn`
when `mutual_tls` is on) a client certificate from the Hive's own authority, issued at approval and
valid for 90 days. Two gaps followed. A certificate that reaches its end left the device locked out
at the handshake, and the only way back was to enrol again: a new invite, a new key and a new
approval for a device the operator already trusts. A device approved while the Hive ran loopback
only was issued no certificate at all, because there was no authority yet, so turning on a remote
mode later meant enrolling every such device again too.

A new certificate is a credential, so how it is obtained has to hold to ADR-0041's rules: approval
decisions exist only on the loopback listener, a stolen session must not be enough to mint a
credential, and the revocation list must name every certificate the Hive no longer honours.

## Decision

**A program renews its own certificate with the key it enrolled.** An approved device with an
Ed25519 key calls `POST /v1/devices/me/certificate/renew` on either listener, logged in as usual
and, under mutual TLS, presenting the certificate it holds. The body is a certificate signing
request; the Hive certifies it only when the request's key is exactly the device's enrolled key,
the one it logs in with. The new certificate names the same device id and lasts another 90 days.
Renewal is allowed only while the current certificate is unwithdrawn and in its last 30 days
(`RENEWAL_WINDOW`): earlier is refused with the date it becomes due, later (or with no certificate)
is refused with a pointer to the operator. A browser cannot make a request, so it never renews; it
is re-issued at the Hive Stand. The renewal needs no step-up: it grants nothing the device does not
already hold, and only the holder of the device key can ask for it.

**The operator re-issues on loopback only.** `POST /v1/devices/{device_id}/certificate`
(`hive entrance reissue`) exists only on the loopback listener, like approval. It certifies the
request the operator gives, or the one the device sent at redemption (which a loopback-only Hive
kept but never signed), again only for the device's enrolled key; for a browser under mutual TLS it
seals a fresh PKCS#12 bundle, answered once and stored nowhere, exactly as approval does. It works
for any APPROVED device, which covers a device approved while the Hive ran loopback only, one whose
certificate lapsed, and one that lost the answer to a renewal.

**What is replaced is withdrawn at once and stays listed until it expires.** A renewal or re-issue
records the new certificate, moves the old one (if still unexpired) to the device's
`superseded_certificates` with `revoked_at` set, and writes one
`guard.entrance_certificate_issued` trail event (how, the new serial and fingerprint, the serial it
replaced), all in one atomic step. The step names the certificate it replaces, so of two renewals
decided from the same certificate only the first is applied. The revocation list is rebuilt right
after, and lists every superseded certificate whatever the device's standing, so a certificate a
device stopped using is never honoured again, and a later revocation still reaches everything the
device ever held. An expired certificate leaves the list, since no handshake accepts it anyway. At
most 16 unexpired superseded certificates are kept; past that a re-issue is refused and the device
is revoked and enrolled again.

The event kind is classified in the policy catalogue at the Entrance route enforcement point
(ADR-0039), like every other edge an Entrance route takes.

## Consequences

Positive: an approved device keeps working past 90 days without a new enrolment, a device approved
before the Hive was exposed gets its certificate without one, and both paths keep the rules of
ADR-0041: no credential is minted from the remote listener without the device's own key, and no
certificate the Hive stopped using is honoured by the next handshake.

Negative: a renewal withdraws the old certificate before the device has stored the new one, so a
device that loses the answer is refused at the next mutual-TLS handshake until the operator
re-issues its certificate on loopback; renewing early in the window leaves room for that. A device
record now carries the certificates it was moved off until they expire, which bounds, rather than
removes, what an operator re-issuing in a loop can store.
