# Contributing

Issues and pull requests are welcome. Include reproduction steps, expected
behavior, desktop/Omarchy version, NetworkManager version, and relevant error
messages. Remove passwords, private network names, and personal data from reports.

Use the distribution's Python/GI packages and run:

```bash
python -m unittest discover -s tests -v
python tests/ui_smoke.py
omarchy plugin validate omarchy
```

The GTK test needs a graphical session and uses a fake backend. Live smoke tests
are documented in the README. Describe which tests are mocked, which use a live
adapter, and whether authentication was actually tested.

Keep networking asynchronous and UI updates event-driven. Preserve saved profile
settings, keep credentials out of arguments/logs/files, and add regression tests
for scan or activation changes. Only test connection changes on networks you are
authorized to use. Contributions are provided under the MIT license.
