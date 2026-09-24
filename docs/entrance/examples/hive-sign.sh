#!/bin/sh
# hive-sign.sh: sign what the HiveMind Landing Board asks a device to sign, so curl can call it.
#
# The Landing Board (the Hive Entrance's API, described by docs/entrance/openapi.json) refuses an
# authenticated request unless it carries X-Hive-Timestamp, X-Hive-Nonce and X-Hive-Signature: the
# session's binding key's signature over hive-request-v1. curl cannot sign, so this script does, for
# a program's Ed25519 device key (which is also its session's binding key), with OpenSSL 3 and POSIX
# tools only. Every string follows the document's x-hive-signing extension: its tag, then its
# fields, one per line, joined by a single line feed (no trailing newline), encoded as UTF-8;
# digests are lowercase hex SHA-256; nonces and signatures are unpadded base64url; timestamps are
# integer Unix seconds. See docs/entrance/landing-board.md for the calls that use it.
#
# Usage:
#   hive-sign.sh public-key KEY.pem                           the raw public key, 64 lowercase hex
#   hive-sign.sh enrol KEY.pem HIVE_ID INVITE_CODE            the signature redeeming an invite
#   hive-sign.sh login KEY.pem HIVE_ID DEVICE_ID NONCE        the signature over a login challenge
#   hive-sign.sh request KEY.pem METHOD TARGET [BODY_FILE]    the three signed-request headers
#   hive-sign.sh socket KEY.pem TOKEN_FILE TARGET             a WebSocket's signed first frame
#
# KEY.pem is the device's Ed25519 private key (openssl genpkey -algorithm ed25519 -out KEY.pem).
# TARGET is the path and query exactly as curl sends them, e.g. /v1/chat?limit=5. BODY_FILE holds
# the exact bytes curl sends with --data-binary @BODY_FILE; leave it out for a request with no body.
# TOKEN_FILE holds the session token, so it never appears on a command line.
#
# Needs: OpenSSL 3.0 or later (pkeyutl -rawin signs raw Ed25519), and date +%s.
set -eu

die() {
    printf 'hive-sign.sh: %s\n' "$1" >&2
    exit 2
}

usage() {
    cat >&2 <<'USAGE'
usage: hive-sign.sh public-key KEY.pem
       hive-sign.sh enrol KEY.pem HIVE_ID INVITE_CODE
       hive-sign.sh login KEY.pem HIVE_ID DEVICE_ID NONCE
       hive-sign.sh request KEY.pem METHOD TARGET [BODY_FILE]
       hive-sign.sh socket KEY.pem TOKEN_FILE TARGET
USAGE
    exit 2
}

# A line feed and a carriage return, for the field check ($(...) would strip a line feed).
newline='
'
carriage_return=$(printf '\r')

# Every temporary file lives in one private directory, removed however the script ends.
umask 077
work=$(mktemp -d) || die "cannot create a temporary directory"
trap 'rm -rf "$work"' EXIT HUP INT TERM

# Unpadded base64url of stdin's bytes.
b64url() {
    openssl base64 -A | tr '+/' '-_' | tr -d '='
}

# Lowercase hex SHA-256 of stdin's bytes.
sha256_hex() {
    openssl dgst -sha256 -r | cut -d ' ' -f 1
}

# Refuse a field that holds a line break: it could forge another line of the signed string.
check_field() {
    case $1 in
    *"$newline"* | *"$carriage_return"*) die "a signed field may not contain a line break" ;;
    esac
    [ -n "$1" ] || die "a signed field may not be empty"
}

# Write the signed string (tag, then fields, one per line, no final newline) and sign it.
# OpenSSL 3.0 signs raw Ed25519 only from a file, so the message goes to one first.
sign() {
    key=$1
    shift
    for field in "$@"; do check_field "$field"; done
    printf '%s' "$1" >"$work/message"
    shift
    for field in "$@"; do printf '\n%s' "$field" >>"$work/message"; done
    openssl pkeyutl -sign -inkey "$key" -rawin -in "$work/message" >"$work/signature" ||
        die "OpenSSL could not sign with $key (an Ed25519 key, OpenSSL 3.0 or later?)"
    b64url <"$work/signature"
}

# The key's raw Ed25519 public key in lowercase hex: the last 32 bytes of its DER encoding.
public_key_hex() {
    openssl pkey -in "$1" -pubout -outform DER | tail -c 32 | od -An -v -tx1 | tr -d ' \n'
}

# An invite code as the Hive Stand showed it: upper case, in dash-joined groups of four.
canonical_code() {
    printf '%s' "$1" | tr -d ' \t-' | tr 'abcdefghijklmnopqrstuvwxyz' 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' |
        sed 's/\(....\)/\1-/g; s/-$//'
}

[ $# -ge 2 ] || usage
command=$1
key=$2
shift 2
[ -r "$key" ] || die "cannot read the key file $key"

case $command in
public-key)
    [ $# -eq 0 ] || usage
    public_key_hex "$key"
    printf '\n'
    ;;
enrol)
    [ $# -eq 2 ] || usage
    public_key=$(public_key_hex "$key")
    code_hash=$(canonical_code "$2" | sha256_hex)
    sign "$key" hive-enrol-v1 "$1" "$code_hash" "$public_key"
    printf '\n'
    ;;
login)
    [ $# -eq 3 ] || usage
    sign "$key" hive-login-v1 "$1" "$2" "$3"
    printf '\n'
    ;;
request)
    [ $# -eq 2 ] || [ $# -eq 3 ] || usage
    method=$(printf '%s' "$1" | tr 'abcdefghijklmnopqrstuvwxyz' 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    if [ $# -eq 3 ]; then
        [ -r "$3" ] || die "cannot read the body file $3"
        digest=$(sha256_hex <"$3")
    else
        digest=$(printf '' | sha256_hex)
    fi
    stamp=$(date +%s)
    nonce=$(openssl rand 16 | b64url)
    signature=$(sign "$key" hive-request-v1 "$method" "$2" "$stamp" "$nonce" "$digest")
    printf 'X-Hive-Timestamp: %s\nX-Hive-Nonce: %s\nX-Hive-Signature: %s\n' \
        "$stamp" "$nonce" "$signature"
    ;;
socket)
    [ $# -eq 2 ] || usage
    [ -r "$1" ] || die "cannot read the token file $1"
    token=$(tr -d ' \n\r' <"$1")
    # A token is base64url; anything else would need escaping inside the JSON frame.
    case $token in *[!A-Za-z0-9_-]* | '') die "the token file does not hold a session token" ;; esac
    stamp=$(date +%s)
    nonce=$(openssl rand 16 | b64url)
    signature=$(sign "$key" hive-ws-v1 "$2" "$stamp" "$nonce")
    printf '{"token":"%s","timestamp":%s,"nonce":"%s","signature":"%s"}\n' \
        "$token" "$stamp" "$nonce" "$signature"
    ;;
*)
    usage
    ;;
esac
