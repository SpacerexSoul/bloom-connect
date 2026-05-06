# M2 — IR Contract Proposal

> Author: mac · 2026-05-06 · Branch: `mac/m2-client-ir`
>
> Status: **DRAFT — awaiting win review on coord channel**
>
> Scope: lock the IR contract for "anything `blpapi` can do" before
> either side starts implementation. Nothing in this doc is canonical
> until win acks.

---

## 1. Where we are after M1

The IR is **already mostly generic** — `CreateRequestOp` /
`AppendOp` / `SetOp` / `SendRequestOp` work for any service +
request type. The actual gaps are narrower than the M2 title implies:

1. **Op naming is mis-leading.** `CollectResponseOp.op` is wire-encoded
   as the literal string `"collect_refdata_response"`, but the executor
   already uses it for both `ReferenceDataRequest` and
   `HistoricalDataRequest` (see `client/data.py:80`). Name no longer
   matches behaviour.
2. **Response normalisation only covers `securityData`.**
   `extract_security_data` in `executor/normalize.py` understands
   `ReferenceDataResponse` (securityData[]) and `HistoricalDataResponse`
   (single securityData with fieldData[] of date+values). It does
   **not** handle `IntradayBarResponse` (`barData.barTickData[]`),
   `IntradayTickResponse` (`tickData.tickData[]`), or any
   schema-introspection response.
3. **Per-field errors are dropped silently.** When a `fieldData` row
   has both valid fields and a `fieldExceptions` element, the valid
   fields land in `data` but the exceptions are not surfaced.
   `_check_security_errors` only catches `securityError`. Verified
   from mac side during M1 review (bad fields disappeared from the
   `ref_data` dict).
4. **No schema introspection.** `SchemaRequest` /
   `FieldInfoRequest` aren't reachable, and there's no per-service
   schema cache. M8 (LLM query builder) needs both.
5. **Validator doesn't gate per-service request types.** Today the
   validator accepts any `(service, request)` pair — the executor
   relies on `blpapi` to reject bad ones at runtime. M2's "validator
   is the trust boundary" rule means the validator should know each
   service's allowed request types and required/forbidden elements.

## 2. Proposed changes

### 2.1 Op rename + bump `protocol_version` to `1.1`

- Add `CollectResponseOp` with wire literal `"collect_response"` (same
  fields: `correlation_id`, `timeout_ms`).
- Keep `CollectRefdataResponseOp` (wire literal `"collect_refdata_response"`)
  as a deprecated alias for one minor version. Validator emits a warning
  in the response `errors` list with code `IR_DEPRECATED_OP` (status stays
  `ok`/`partial`).
- Bump `ExecutionPlan.protocol_version` default to `"1.1"`. Server
  accepts `"1.0"` and `"1.1"`. `"1.2"` will retire the deprecated alias.
- Server reads from `Op` `op` literal as today; no wire-protocol break
  for legacy clients.

### 2.2 Response normalisation — dispatch on root element

Replace `extract_security_data` with a dispatcher in
`executor/normalize.py`:

```python
def normalize_message(message) -> NormalizedMessage:
    if message.hasElement("securityData"):
        return _normalize_security_data(message)        # refdata + historical
    if message.hasElement("barData"):
        return _normalize_bar_data(message)             # intraday bars
    if message.hasElement("tickData"):
        return _normalize_tick_data(message)            # intraday ticks
    if message.hasElement("fields"):
        return _normalize_field_info(message)           # FieldInfoResponse
    if message.hasElement("schema") or message.hasElement("metaData"):
        return _normalize_schema(message)               # //blp/refdata schema
    return _normalize_generic(message)                  # fallback: dict-walk
```

Each handler returns a `NormalizedMessage` (dataclass) with three
fields: `data: dict[str, Any]`, `errors: list[ErrorDetail]`,
`warnings: list[ErrorDetail]`. The executor merges them into the
existing `ExecutionResult`.

Generic fallback uses element walking (the existing `_normalize_value`
recursion) so unknown response types still produce something readable
instead of an empty dict.

### 2.3 Per-field error surface

Inside `_normalize_security_data`:

- For each security record, scan `fieldExceptions` (existing element,
  not currently read):

  ```
  fieldExceptions[]:
    fieldId         → string  (BBG field id, e.g. "PX_LAST_BAD")
    errorInfo:
      source        → string
      code          → int
      category      → string  (BAD_FLD, NOT_FOUND, etc.)
      message       → string
  ```

- Each exception becomes an `ErrorDetail` with:
  `code = "BLP_FIELD_" + category` (e.g. `BLP_FIELD_BAD_FLD`),
  `message = message`,
  `security = security_name`,
  `field = fieldId`.

- These go into the result `errors` list. Executor still returns
  `status="partial"` if any data came through; `status="error"` only if
  no data at all.

`ErrorDetail` already has `security` and `field` optional fields —
no schema change needed.

### 2.4 Schema cache (server-side)

New module: `blpremote_server/schema_cache.py`.

- `SchemaCache` singleton, started by lifespan after `SessionManager`.
- On first request hitting a service, fire `SchemaRequest` once,
  store the parsed schema (dict of request_name → element tree).
- Optional pre-warm at boot for `//blp/refdata` and `//blp/news` (env
  flag `BLPREMOTE_SCHEMA_PREWARM=1`).
- TTL: forever, refreshed on session reconnect (schemas are essentially
  static during a Terminal session).
- Public read API: `cache.get(service)` returns `dict | None`.

This unblocks M8 cheaply — LLM context becomes "here are the valid
request types and elements for the service you picked."

### 2.5 Validator extensions

`executor/validate.py` learns:

- A small allow-table of `(service → set of valid request types)` for
  the services we explicitly support: `//blp/refdata`,
  `//blp/news`, `//blp/apiflds`, `//blp/instruments`. Anything outside
  this set is allowed but flagged as `warnings` with code
  `IR_UNVERIFIED_SERVICE`.
- Per-request required element checks where they're cheap and
  unambiguous:
  - `ReferenceDataRequest`: `securities[] ≥ 1`, `fields[] ≥ 1`
  - `HistoricalDataRequest`: same + `startDate`, `endDate`, optional
    `periodicityAdjustment`/`periodicitySelection`
  - `IntradayBarRequest`: `security`, `eventType`, `interval`,
    `startDateTime`, `endDateTime`
  - `IntradayTickRequest`: `security`, `eventTypes[] ≥ 1`,
    `startDateTime`, `endDateTime`
- The existing limits (`max_securities`, `max_fields`, `max_timeout_ms`)
  remain.
- Validator returns the same `PlanValidationError` on hard failures.

This is the "trust boundary" — once an LLM-generated plan passes the
validator, it's known-shaped and known-bounded, even if BBG ultimately
rejects it for other reasons.

## 3. Client surface (mac side, M2 deliverables)

- Convenience funcs in `blpremote_client.data`:
  - `get_history(host, security, fields, start, end, periodicity="DAILY")`
    — already exists as `get_historical_prices`; rename + generalise
    fields.
  - `get_bars(host, security, event_type, start, end, interval=1)`
    → IntradayBarRequest, returns DataFrame-compatible dict.
  - `get_ticks(host, security, event_types, start, end)`
    → IntradayTickRequest.
- `proxy/session.Session.collectResponse` updated to use
  `collect_response` op (no behaviour change for the user; wire op
  changes).
- New `client.schema` module:
  - `get_field_info(host, field_ids: list[str]) -> dict`
  - `get_service_schema(host, service: str) -> dict` (uses the server
    schema cache via a passthrough endpoint TBD with win — see §5).
- All new surfaces tested against a mock server in
  `tests/test_data_history.py` / `test_data_bars.py` / etc. Live
  verification done from win side per the cross-boundary rule.

## 4. Out of scope for M2

- Subscriptions (M3). The PoC validated the session model; the IR
  + client surface for subscribes/SSE are M3.
- DataFrame-native (`pd_history()`, `pl_history()`) — that's M7,
  built on top of the dicts M2 returns.
- LLM piece (M8). Schema cache (§2.4) is the M2 prerequisite; the
  query builder itself comes later.
- Per-API-key rate limits (backlog, post-M5).

## 5. Open coordination questions for win

1. **Schema cache exposure.** Do you want `get_service_schema` to be
   a dedicated endpoint (`GET /v1/schema/{service}` with auth) so the
   client doesn't have to build a SchemaRequest IR every time? My
   preference: yes — this is a read-mostly cache hit on the server,
   pretending it's a regular IR plan adds latency and noise to the
   audit log (M4 JSONL).

2. **Deprecated op alias.** OK to keep `collect_refdata_response`
   accepting for one minor version, or do you prefer a clean break
   on protocol_version="1.1"? Clean break is fewer code paths but
   any Krishna-side script pinned to 1.0 stops working.

3. **Validator allow-table location.** Hard-coded in `validate.py`
   for the four services I listed, or driven from the schema cache
   once warm? My preference: hard-coded for M2 (cache may not be
   warm at first plan; we want the validator to fail fast). Refactor
   to schema-driven in M8 when we need it for the LLM context anyway.

4. **fieldExceptions handler test data.** Do you have a saved BBG
   response with a mix of valid + invalid fields you can share, or
   should I synthesise one for the unit test? Mac side can mock the
   message structure but a real BBG payload would be the only way
   to be sure I've got the exception schema right.

5. **Sequence within M2.** Proposing: (a) IR rename + protocol bump
   (small, mac-driven); (b) per-field errors (small, win-driven on
   server, mac follows with assertion in tests); (c) bar / tick
   normalisers (win); (d) schema cache + endpoint (win); (e) client
   convenience funcs + tests (mac, depends on a-d). Reasonable, or
   reorder?

## 6. Acceptance criteria

M2 is `done` when:

- [ ] `protocol_version="1.1"` accepted by server; `"1.0"` still works.
- [ ] `collect_response` op wires through; `collect_refdata_response`
      still works with deprecation warning.
- [ ] Bar + tick + field-info responses normalise and round-trip
      cleanly through the client.
- [ ] A `ref_data` call with a known-bad field surfaces the field
      exception in `errors` and still returns the valid fields in
      `data`. (Mac asserts; win verifies live.)
- [ ] Schema cache populated on first request to `//blp/refdata`;
      cache hit confirmed on second request via /metrics counter
      (M4 prerequisite — for now just a log line).
- [ ] Validator rejects an `IntradayBarRequest` missing `interval`.
- [ ] Both-side verification posted on coord per the working
      agreement in PLAN.md.

— mac
