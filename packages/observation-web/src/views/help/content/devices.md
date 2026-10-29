The Hive has one door and one keeper. Everything that talks to the Hive from outside, this app
included, comes through the Hive Entrance, and only a device enrolled and approved at the Hive
Stand may land on its Landing Board.

## The Hive Entrance and the Landing Board

The Hive Entrance is an authenticated HTTP and WebSocket gateway on the Hive Stand. It runs two
listeners: **loopback**, always on, which only software on the Hive Stand reaches; and **remote**,
only when the Hive is exposed, over a VPN overlay by default, with mutual TLS for LAN and tunnel
modes. There is no public mode. The **Landing Board** is its versioned, published API, named for
the platform at a real hive's entrance where every bee lands before going in. The top bar says
which listener this device came in through: `on the Hive Stand` or `remote`.

## Enrolling a device

A phone, laptop or program cannot even try to log in until it is enrolled:

- You mint a short-lived, single-use **invite** on the Hive Stand (the [Enrol screen](#/enrol) or
  the command line), as a code and a QR.
- The device opens the invite, makes its own key (a passkey in a browser, a signed key in a
  program) and presents it. The request sits pending.
- You approve it on the Hive Stand with a name, a set of capabilities, a spend cap and an expiry,
  after checking its fingerprint. Or you deny it.

Approval, widening, unlock and revocation exist only on the loopback listener, so even a fully
compromised remote session cannot admit a new device. A manifest switch, off by default, lets one
enrolled device act as a steward that may approve others after full step-up.

## Logging in

Every login is two factors, and one of them is the device: the device's key (a passkey with a
biometric or PIN, or a signed challenge for a program) plus the operator's password. Sessions are
short, bound to the device key so a stolen token is useless without the device, and idle out. A
session is revoked the instant its device is.

## The Hive Stand and the stand key

The Hive Stand is the machine HiveMind runs on: home of the Queen, the first Real Cell, and where
every Warden lives by default. It is a role rather than a fixed machine, since Supersedure moves
it.

A Virtual Cell's guest can reach the host's loopback address, so "only the Hive Stand reaches
loopback" is not enough on its own. Each time the Entrance starts it draws a **stand key**, kept in
memory on the Hive Stand and never given to any Cell. The routes that admit or empower a device
need it, and the Hive Stand's own browser tab carries it. A screen that refuses with "Only the Hive
Stand's own browser tab can do this" is asking for that key.

## Capabilities and spend caps

A device is approved with a small capability set: watch the Hive, read bees' thoughts, read Honey
folders, send goals and messages, answer questions, receive push, manage devices. A program usually
gets submit, answer and observe plus its own daily spend cap, and can never hold more than you
granted on the Hive Stand. The [Devices screen](#/devices) lists each device's capabilities in
words.

## Step-up

Sensitive actions need fresh step-up within the last few minutes: both factors again. Spend above a
cap, key and capability changes, Supersedure, Sting Cut, Absconding and reopening a reduced
Entrance all ask for it. A session that needs step-up shows a banner under the top bar until the
prompt is done.

## Guard Bees at the door

Lockout after a few failed attempts per device, rate limits per device and per address, and every
enrolment, approval, denial, revocation, lockout and step-up on the Pheromone Trail and pushed to
every other enrolled device, so you notice anything you did not do. The **Entrance Reducer** drops
the Entrance to loopback only and ends every remote session, on one command or on a Guard Bee's
rule when failures burst; only the Hive Stand reopens it, with step-up. An optional travel lock
forces step-up and a notification when a known device appears from a new network.

## Push, not polling

Anything that needs you is pushed to whichever enrolled device you are on: a question, an Alarm
that reached the top, a reply from the Queen, the completion of a goal that device submitted, and
every security event. A push says only that something is waiting, never the content, because it
transits third-party push services. The notice bar under the strip shows what is waiting on every
screen; a question answered on one device is withdrawn from every other.

## The Swarm and the Pollen Packet

A device can also be a Real Cell. A **Pollen Packet** is the thin gateway installed on an external
device: it enrols the device with the Queen, reports what the device is and what it can bear, and
opens a signed terminal session. It has no brain of its own; the device's Warden lives on the Hive
Stand and drives it over that session. The device climbs a ladder as the Hive needs it: gateway
only, then the Warden moves onto the device (colonized), then a model server starts there too (a
Nuc, which keeps working when cut off). The Warden always arrives before the models.

## Voice and images

An enrolled device may send audio instead of text: push-to-talk in this app, or a clip shared from
another app on a phone. The Entrance transcribes it on the transcriber slot, Whisper by default and
local first. A spoken goal is shown back for confirmation before it becomes a task, so a misheard
sentence never spends anything. Audio, transcripts and images from a client are Royal.
