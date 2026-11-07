# Implementation notes

```text
Omarchy bar widget (QML) ─┐
Meta+N / app launcher ────┴─> GTK4 + libadwaita window
                                  │
                             Python libnm
                                  │ D-Bus
                             NetworkManager
                                  │
                         configured Wi-Fi backend
                                  │
                           driver and radio
```

## Scanning and activation

The backend creates an asynchronous `NM.Client`, enumerates cached access points,
and subscribes to adapter, AP, and connection-state changes. Idle callbacks
coalesce notification bursts. Existing row widgets survive refreshes so a scan
does not discard a password being typed.

`request_scan_async` requests scanning; acceptance is not treated as completion.
Completion follows `LastScan`. Scan generations keep an old callback from ending
a newer request. Cached results remain visible if scanning is deferred or times out.

Networks are grouped by raw SSID bytes, security, and adapter. Names are plain
text. The active AP is preferred over a stronger roaming candidate. An AP is
resolved again before activation to reject stale rows. Compatible saved profiles
are reused; new profiles are validated with libnm. Success is reported only when
the active connection reaches `ACTIVATED`.

## Original comparison

The investigation on 7 November 2026 inspected Omarchy 4.0.6, Quickshell commit
`2b4501a`, and Adw Network commit `59df66eafba8a010f3302dad1923a2f6dad5abe5`.

| Interface | Backend | Inspected discovery behavior |
| --- | --- | --- |
| Omarchy default | Quickshell networking over NM D-Bus | 10,001 ms interval based on last scan and last request |
| Adw Network | Rust/zbus over NM D-Bus | Direct scan request; cached APs merged with a read after 1,200 ms |
| Quick Wi-Fi | Python/libnm over NM D-Bus | Cached APs first; direct scan request and NM events |

Quickshell's comments describe rate limiting but do not explain the exact choice
of 10,001 ms. It is not an unconditional delay on every panel open: a recent scan
causes the next request to wait for the remaining interval.

Scanning can take the radio away from the current AP's channel and affect latency.
Quick Wi-Fi keeps periodic scanning bounded while allowing explicit refresh
without Quickshell's extra gate. NetworkManager can still defer requests. GTK
itself is not the reason this is possible; a Quickshell UI with a different backend
could use the same strategy.

These findings are version-specific, not a claim that every Omarchy version is
slow or that authentication will be faster. The bundled network panel updates
with Omarchy, rather than the community-plugin Git-update command.

## References

- [Quickshell scanner](https://github.com/quickshell-mirror/quickshell/blob/2b4501a/src/network/nm/wireless.cpp#L155)
- [Quickshell interval](https://github.com/quickshell-mirror/quickshell/blob/2b4501a/src/network/nm/wireless.hpp#L86)
- [Adw Network scanning](https://github.com/PlayRood32/adw-network/blob/59df66eafba8a010f3302dad1923a2f6dad5abe5/src/nm.rs#L410)
- [Adw Network D-Bus requests](https://github.com/PlayRood32/adw-network/blob/59df66eafba8a010f3302dad1923a2f6dad5abe5/src/nm_dbus.rs#L416)
- [NetworkManager scan tradeoffs](https://blogs.gnome.org/dcbw/2016/05/16/networkmanager-and-wifi-scans/)
