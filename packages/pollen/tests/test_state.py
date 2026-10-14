"""Test what the packet persists: the key, the state document and the data layout.

The Pollen Packet (the thin gateway program on a device the Hive borrows) survives restarts because
everything it was told at enrolment lives in its data directory: the node's Ed25519 key in a file of
its own (mode 0600, never sent anywhere) and ``PacketState``, one JSON document holding the
device's identity, the Queen's address and pins, the grants and the highest relocation notice
honoured. Every write is write-new, flush, replace-old, so a crash leaves the old document or the
new one. ``StateStore`` is the one owner of the mutable copy: an update is validated, written, and
only then visible. The state is a boundary model, so it round-trips and refuses what the link rules
refuse (a plaintext address without a loopback host, a pin on a plain link, a tier a Real Cell
cannot hold). It also records the rung of the runtime ladder the device is on and the Warden that
lives on the device when it is above the bottom one; the two are written together and may never
disagree, because the dead-man switch stands down exactly while a Warden lives there.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.state.

Key invariants:
    - A failed write leaves the old file; a failed validation leaves the old value.
    - The key file holds exactly the 32-byte seed and is private.
    - A Warden is recorded exactly when the level is above GATEWAY, in both directions.

See Also:
    - docs/waggle/gateway-contract.md section 4.
"""

from __future__ import annotations

import asyncio
import stat
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from states import sample_state

from pollen.errors import NotEnrolledError, StateError
from pollen.state import (
    DataLayout,
    InstallSettings,
    PacketState,
    QueenMove,
    StateStore,
    WardenRecord,
    create_key,
    load_signer,
    load_state,
    save_state,
    write_atomic,
)
from waggle.clock import FakeClock
from waggle.ids import new_lease_id, new_node_id
from waggle.messages.labels import CombShieldLevel
from waggle.messages.swarm import RuntimeLevel

CLOCK = FakeClock()
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


def test_every_file_has_one_name_under_the_data_directory(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path / "pollen")
    lease = new_lease_id(CLOCK)
    assert layout.state.parent == layout.key.parent == layout.root
    assert layout.scratch(lease) == layout.root / "leases" / lease
    assert layout.backups(lease) == layout.root / "restore" / lease
    assert len({layout.state, layout.key, layout.outbox, layout.lease_table, layout.status}) == 5
    layout.ensure()
    layout.ensure()
    assert layout.root.is_dir()


def test_the_runtime_lives_in_one_folder_of_the_data_directory(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path / "pollen")
    assert layout.runtime == layout.root / "runtime"
    assert layout.venv.parent == layout.warden_state.parent == layout.runtime
    assert layout.warden_log.parent == layout.prepare_log.parent == layout.runtime
    assert len({layout.venv, layout.warden_state, layout.warden_log, layout.prepare_log}) == 4
    assert layout.runtime not in {layout.state, layout.key, layout.outbox, layout.status}


@posix_only
def test_the_data_directory_is_private(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path / "pollen")
    layout.ensure()
    assert stat.S_IMODE(layout.root.stat().st_mode) == 0o700


def test_the_state_round_trips_through_its_json_form() -> None:
    state = sample_state(CLOCK, install=InstallSettings(runtime_index="https://x.example/simple"))
    assert PacketState.model_validate_json(state.model_dump_json()) == state


def test_a_relocation_record_round_trips_and_a_wrong_key_is_refused() -> None:
    move = QueenMove(
        sequence=1,
        previous_url="ws://127.0.0.1:1",
        previous_node_id=new_node_id(CLOCK),
        previous_key_hex="ab" * 32,
        new_url="ws://127.0.0.1:2",
        new_node_id=new_node_id(CLOCK),
        new_key_hex="cd" * 32,
        effective_at=CLOCK.now(),
        grace_until=CLOCK.now() + timedelta(hours=1),
    )
    assert QueenMove.model_validate_json(move.model_dump_json()) == move
    with pytest.raises(ValidationError):
        QueenMove.model_validate({**move.model_dump(), "new_key_hex": "short"})
    with pytest.raises(ValidationError):
        QueenMove.model_validate({**move.model_dump(), "sequence": 0})


def _warden(**changes: object) -> WardenRecord:
    """A recorded Warden; ``changes`` replace fields."""
    values: dict[str, object] = {
        "pid": 4242,
        "created": 1_700_000_000.5,
        "runtime_version": "1.4.0",
        "started_at": CLOCK.now(),
    }
    values.update(changes)
    return WardenRecord(**values)


def test_a_warden_is_recorded_exactly_when_the_level_is_above_gateway() -> None:
    colonized = sample_state(CLOCK, level=RuntimeLevel.COLONIZED, warden=_warden())
    assert PacketState.model_validate_json(colonized.model_dump_json()) == colonized
    assert colonized.warden is not None and colonized.warden.pid == 4242
    assert sample_state(CLOCK).warden is None and sample_state(CLOCK).level is RuntimeLevel.GATEWAY
    with pytest.raises(ValidationError, match="exactly when the level is above GATEWAY"):
        sample_state(CLOCK, level=RuntimeLevel.COLONIZED)  # Above the bottom rung, no Warden.
    with pytest.raises(ValidationError, match="exactly when the level is above GATEWAY"):
        sample_state(CLOCK, warden=_warden())  # A Warden on the bottom rung.
    with pytest.raises(ValidationError, match="exactly when the level is above GATEWAY"):
        sample_state(CLOCK, level=RuntimeLevel.NUC)


@pytest.mark.parametrize(
    "changes",
    [{"pid": 0}, {"created": -1.0}, {"runtime_version": ""}, {"surprise": 1}],
)
def test_a_warden_record_refuses_what_cannot_name_a_process(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _warden(**changes)


def test_a_state_written_before_colonization_existed_still_loads() -> None:
    old = sample_state(CLOCK).model_dump(mode="json")
    del old["warden"], old["install"]["runtime_python"]
    loaded = PacketState.model_validate(old)
    assert loaded.warden is None and loaded.install.runtime_python is None
    assert loaded.level is RuntimeLevel.GATEWAY


def test_install_settings_round_trip_and_refuse_two_runtime_sources() -> None:
    settings = InstallSettings(runtime_wheelhouse="/opt/wheels", warden_manifest="/etc/w.toml")
    assert InstallSettings.model_validate_json(settings.model_dump_json()) == settings
    with pytest.raises(ValidationError, match="not both"):
        InstallSettings(runtime_wheelhouse="/opt/wheels", runtime_index="https://x.example")


def test_install_settings_record_the_interpreter_the_runtime_is_built_with() -> None:
    settings = InstallSettings(
        runtime_index="https://x.example/simple", runtime_python="/usr/bin/p"
    )
    assert InstallSettings.model_validate_json(settings.model_dump_json()) == settings
    assert InstallSettings().runtime_python is None


@pytest.mark.parametrize(
    "changes",
    [
        {"queen_url": "wss://hive.example.net:8720"},
        {"queen_url": "ws://hive.example.net:8720"},
        {"tls_pin_sha256": "ab" * 32},
        {"comb_shield": CombShieldLevel.NIGHT_VEIL},
        {"heartbeat_interval_s": 0},
        {"hive_key_hex": "nope"},
        {"surprise": 1},
    ],
)
def test_the_state_refuses_what_the_link_rules_refuse(changes: dict[str, object]) -> None:
    good = sample_state(CLOCK).model_dump(mode="json")
    with pytest.raises(ValidationError):
        PacketState.model_validate({**good, **changes})


def test_a_pinned_wss_state_is_valid() -> None:
    state = sample_state(CLOCK, queen_url="wss://hive.example.net:8720", tls_pin_sha256="ab" * 32)
    assert state.tls_pin_sha256 == "ab" * 32


@posix_only
def test_writing_replaces_the_file_whole_and_privately(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "doc.json"
    write_atomic(path, b"one")
    write_atomic(path, b"two")
    assert path.read_bytes() == b"two"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["doc.json"]


def test_a_failed_write_leaves_the_old_file_and_no_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "doc.json"
    write_atomic(path, b"old")

    def refuse(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("pollen.state.os.replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        write_atomic(path, b"new")
    assert path.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["doc.json"]


@posix_only
def test_the_key_is_created_private_and_reloads_to_the_same_signer(tmp_path: Path) -> None:
    path = tmp_path / "node.ed25519"
    signer = create_key(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and len(path.read_bytes()) == 32
    assert load_signer(path).public_key_bytes == signer.public_key_bytes


def test_a_key_is_never_replaced_unless_asked(tmp_path: Path) -> None:
    path = tmp_path / "node.ed25519"
    first = create_key(path)
    with pytest.raises(StateError, match="already exists"):
        create_key(path)
    assert create_key(path, replace=True).public_key_bytes != first.public_key_bytes


def test_a_key_that_is_missing_or_damaged_is_a_clear_failure(tmp_path: Path) -> None:
    with pytest.raises(NotEnrolledError):
        load_signer(tmp_path / "absent")
    short = tmp_path / "short"
    short.write_bytes(b"too short")
    with pytest.raises(StateError, match="32-byte"):
        load_signer(short)
    with pytest.raises(StateError):
        load_signer(tmp_path)  # A directory is not a key file.


def test_a_state_file_that_is_missing_unreadable_or_invalid_is_a_clear_failure(
    tmp_path: Path,
) -> None:
    with pytest.raises(NotEnrolledError):
        load_state(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_text('{"device_id": "nonsense"}')
    with pytest.raises(StateError, match="device_id"):
        load_state(broken)
    with pytest.raises(StateError):
        load_state(tmp_path)


def test_saving_and_loading_the_state_agree(tmp_path: Path) -> None:
    state = sample_state(CLOCK)
    save_state(tmp_path / "state.json", state)
    assert load_state(tmp_path / "state.json") == state


def test_saving_to_an_unwritable_place_is_a_state_error(tmp_path: Path) -> None:
    (tmp_path / "blocked").write_text("a file where the directory should be")
    with pytest.raises(StateError):
        save_state(tmp_path / "blocked" / "state.json", sample_state(CLOCK))
    with pytest.raises(StateError):
        create_key(tmp_path / "blocked" / "node.ed25519")


async def test_an_update_is_validated_written_and_then_visible(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path, sample_state(CLOCK))
    updated = await store.update(offline_limit_s=77.0, moved_sequence=3)
    assert store.current is updated
    assert load_state(path).offline_limit_s == 77.0 and load_state(path).moved_sequence == 3


async def test_an_update_that_fails_validation_or_writing_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    first = sample_state(CLOCK)
    save_state(path, first)
    store = StateStore.load(path)
    with pytest.raises(StateError, match="validation"):
        await store.update(heartbeat_interval_s=-1)

    def refuse(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("pollen.state.write_atomic", refuse)
    with pytest.raises(StateError):
        await store.update(offline_limit_s=5.0)
    assert store.current == first and load_state(path) == first


async def test_the_level_and_the_warden_change_in_one_write_or_not_at_all(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path, sample_state(CLOCK))
    with pytest.raises(StateError, match="validation"):
        await store.update(level=RuntimeLevel.COLONIZED)  # The level alone would disagree.
    assert store.current.level is RuntimeLevel.GATEWAY and not path.exists()
    record = _warden()
    await store.update(level=RuntimeLevel.COLONIZED, warden=record)
    assert (load_state(path).level, load_state(path).warden) == (RuntimeLevel.COLONIZED, record)
    with pytest.raises(StateError, match="validation"):
        await store.update(warden=None)  # Nor may the record go while the level stays.
    await store.update(level=RuntimeLevel.GATEWAY, warden=None)
    assert load_state(path).warden is None


async def test_concurrent_updates_do_not_lose_each_other(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json", sample_state(CLOCK))
    await asyncio.gather(
        store.update(offline_limit_s=11.0),
        store.update(heartbeat_interval_s=3.0),
        store.update(moved_sequence=4),
    )
    saved = load_state(tmp_path / "state.json")
    assert (saved.offline_limit_s, saved.heartbeat_interval_s, saved.moved_sequence) == (
        11.0,
        3.0,
        4,
    )


async def test_a_transform_decides_from_the_value_under_the_lock(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json", sample_state(CLOCK))
    assert await store.transform(lambda state: None) is None
    assert await store.transform(lambda state: state) is None  # Unchanged: nothing written.
    assert not (tmp_path / "state.json").exists()
    changed = await store.transform(lambda s: s.model_copy(update={"moved_sequence": 9}))
    assert changed is not None and store.current.moved_sequence == 9
    assert load_state(tmp_path / "state.json").moved_sequence == 9


async def test_a_transform_that_breaks_the_link_rules_is_refused(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json", sample_state(CLOCK))
    with pytest.raises(StateError, match="validation"):
        await store.transform(
            lambda s: s.model_copy(update={"queen_url": "wss://hive.example.net:8720"})
        )
    assert store.current.queen_url == "ws://127.0.0.1:8720"
