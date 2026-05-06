# M2 — IR Contract

> Authors: mac (proposal), win (review) · 2026-05-06 · Branch: `mac/m2-client-ir`
>
> Status: **LOCKED 2026-05-06 — implementation may begin**
>
> Scope: defines the IR contract for "anything `blpapi` can do".
> Negotiated via coord on 2026-05-06; both sides have signed off.
> Subsequent edits should bump `## Revision history` at the bottom
> and ping coord.

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
  as a deprecated alias for one minor version. Validator emits a
  notice in the response `warnings` list (see §2.6) with code
  `IR_DEPRECATED_OP`. Status stays `ok`/`partial`.
- Bump `ExecutionPlan.protocol_version` default to `"1.1"`. Server
  accepts `"1.0"` and `"1.1"`. `"1.2"` will retire the deprecated alias.
- Both ops route to the same handler in the executor for the M2
  duration — no behavioural divergence, only the wire literal differs.
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
    if message.hasElement("fieldData") and not message.hasElement("securityData"):
        return _normalize_field_info(message)           # FieldInfoResponse (fieldData[] of {id, fieldInfo})
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

Test fixture: `packages/blpremote_server/tests/fixtures/refdata_with_field_exceptions.json`
(synthesised from blpapi docs in M2 phase 1; replaced with a real
captured response in phase 2 once win has authorisation to run the
capture script against live BBG).

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

### 2.6 `ExecutionResult.warnings` (new field)

Today `ExecutionResult` has `data`, `errors`, `status`. Add:

```python
warnings: list[ErrorDetail] = Field(default_factory=list)
```

`warnings` carry advisory items that didn't materially affect the
result: deprecated-op notices, unverified-service flags, schema
fallback messages. Distinguishes from `errors` (which mean some part
of the request failed or returned partial data).

Affected surfaces:
- `models.ExecutionResult` server-side and client-side.
- `NormalizedMessage` dataclass introduced in §2.2 — its `warnings`
  field plumbs through to `ExecutionResult.warnings`.
- Status calculation unchanged: `ok` if no errors, `partial` if
  errors AND data, `error` if errors AND no data. Warnings never
  affect status.

Slight breakage for clients that only check `result.errors` — they
miss deprecation notices but otherwise behave correctly. Mitigation:
client convenience funcs surface warnings via a `warnings` parameter
on the response wrapper (`ref_data`, `get_history`, etc).

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

## 5. Decisions (locked 2026-05-06)

1. **Schema cache exposure.** Dedicated endpoint:
   `GET /v1/schema/{service:path}` (path converter so the `//blp/foo`
   slashes survive), bearer auth required. Server reads from the
   `SchemaCache` (§2.4); supports `If-None-Match` returning 304 when
   the cached schema hasn't refreshed since the client's last fetch.
   Excluded from the M4 audit log (read-mostly, sub-ms when warm).
   IR-passthrough for `SchemaRequest` stays available for completeness;
   convenience surface uses the dedicated endpoint.

2. **Deprecated op alias.** `collect_refdata_response` remains
   accepting through `protocol_version="1.1"`; retired in `"1.2"`.
   The `IR_DEPRECATED_OP` notice goes in `warnings` (§2.6), not
   `errors`. Keeps an unknown legacy script (Krishna may have a few)
   working through the transition.

3. **Validator allow-table location.** Hard-coded in `validate.py`
   for `//blp/refdata`, `//blp/news`, `//blp/apiflds`,
   `//blp/instruments`. Anything else triggers an
   `IR_UNVERIFIED_SERVICE` warning (in `warnings`, §2.6). Hard-coded
   table stays even after M8 layers schema-driven validation on top —
   it's the fallback that means the validator never has to phone home.

4. **fieldExceptions fixture.** Two-phase:
   - **Phase 1** (now, mac-driven): synthesise the fixture from blpapi
     docs — JSON shape per win's spec at the bottom of this section.
     Stored at
     `packages/blpremote_server/tests/fixtures/refdata_with_field_exceptions.json`.
     Unblocks (b) without waiting on live capture.
   - **Phase 2** (when win has authorisation, win-driven): capture one
     real response, diff against the synthesised fixture, replace and
     delete the synth. Likely the only deltas are `source` strings and
     numeric `code` values.

   Synthesised fixture shape (per win):
   ```
   fieldExceptions[]:
     fieldId         String  ("THIS_FIELD_IS_NOT_REAL")
     errorInfo:
       source        String  ("9::bbdbh1" or similar)
       code          Int32   (e.g. 5)
       category      String  ("BAD_FLD" / "NOT_APPLICABLE_TO_REF_DATA" / ...)
       subcategory   String  ("INVALID_FIELD")
       message       String  human-readable
   ```

5. **Sequence within M2.** Steps (a) and (b) run in parallel (different
   files; no wire conflict — both ops route to the same handler):
   - (a) IR rename + protocol bump + `warnings` field — **mac**.
     Touches `models.py` (server + client), executor wire dispatch.
   - (b) `fieldExceptions` per-field error surface — **win**. Touches
     `executor/normalize.py` + validator. Uses the phase-1 fixture
     mac ships in (a).
   Then sequential:
   - (c) bar + tick + field-info normalisers — **win**.
   - (d) schema cache + dedicated endpoint — **win**.
   - (e) client convenience funcs + schema client + tests — **mac**.
     Depends on (a)–(d).

## 6. Acceptance criteria

M2 is `done` when:

- [ ] `protocol_version="1.1"` accepted by server; `"1.0"` still works.
- [ ] `collect_response` op wires through; `collect_refdata_response`
      still works and surfaces an `IR_DEPRECATED_OP` notice in the
      result `warnings` list.
- [ ] `ExecutionResult.warnings` round-trips end-to-end (server →
      client) and is exposed to convenience-func callers.
- [ ] Intraday bar response: a valid `IntradayBarRequest` returns
      `≥1` bar through the client. (Empty-data must not pass.)
- [ ] Intraday tick response: a valid `IntradayTickRequest` returns
      `≥1` tick. (Empty-data must not pass.)
- [ ] FieldInfo response normalises and round-trips through the client.
- [ ] A `ref_data` call with a known-bad field surfaces the field
      exception in `errors` (with `security` + `field` populated) and
      still returns the valid fields in `data`. (Mac asserts against
      phase-1 fixture; win verifies live and phase-2 fixture replaces.)
- [ ] Schema cache populated on first request to `//blp/refdata`;
      cache hit confirmed on second request (log line for M2; counter
      lands with M4).
- [ ] `GET /v1/schema/{service:path}` returns the cached schema with
      bearer auth; supports `If-None-Match` → 304.
- [ ] Validator rejects an `IntradayBarRequest` missing `interval`.
- [ ] Validator emits `IR_UNVERIFIED_SERVICE` warning for unknown
      services.
- [ ] Both-side verification posted on coord per the working
      agreement in PLAN.md.

## Revision history

- **2026-05-06 r1** — initial proposal, mac.
- **2026-05-06 r2 (LOCKED)** — win review folded in: dedicated schema
  endpoint with `If-None-Match`/304, `warnings` list on
  `ExecutionResult`, `fieldData` (not `fields`) as FieldInfoResponse
  dispatcher key, two-phase fieldExceptions fixture, parallel (a)+(b)
  sequence, ≥1-bar/tick acceptance criteria.
