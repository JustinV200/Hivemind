-- Roadmap step 10.5b: the push tables (codingrules Appendix C, "push subscriptions"; ADR-0042), in
-- their own migration series, "entrance_push", beside the Entrance tables in the Hive's one file.
--
-- Rows follow the Entrance tables' shape: the columns a query filters or orders by, plus `body`,
-- the record's full pydantic JSON (hivemind.entrance.push.models.Subscription), the source of
-- truth every read decodes. Timestamps are datetime.isoformat() UTC strings, which sort lexically
-- in time order (ADR-0006). device_id is deliberately not a foreign key into entrance_devices:
-- that table belongs to another series, and the dispatcher deletes a device's subscriptions when
-- it leaves APPROVED and re-validates them all on start.

-- One row per registered webhook or Web Push subscription. A device registers one destination
-- once per channel: registering the same endpoint again is recognised, not duplicated.
CREATE TABLE IF NOT EXISTS entrance_push_subscriptions (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL,
    UNIQUE (device_id, channel, endpoint)
);

-- list_for_device and its order: "every subscription of this device, oldest first".
CREATE INDEX IF NOT EXISTS idx_entrance_push_subscriptions_device
    ON entrance_push_subscriptions (device_id, created_at, id);

-- The delivery log: which subscriptions a notice about each ref reached, so a withdrawal reaches
-- exactly those. ON DELETE CASCADE removes a subscription's rows with it (connect() turns
-- foreign keys on), so the log never names a destination that is gone.
CREATE TABLE IF NOT EXISTS entrance_push_deliveries (
    ref TEXT NOT NULL,
    subscription_id TEXT NOT NULL
        REFERENCES entrance_push_subscriptions (id) ON DELETE CASCADE,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (ref, subscription_id)
);

-- The cascade above looks rows up by subscription; without this it would scan the whole log.
CREATE INDEX IF NOT EXISTS idx_entrance_push_deliveries_subscription
    ON entrance_push_deliveries (subscription_id);
