# Attribution

- Source: https://github.com/GoogleCloudPlatform/cloud-release-chat-bot/tree/d0463c4e43fe/release
- Commit: d0463c4e43fe
- Licence: Apache-2.0 (repo-root LICENSE, vendored here as `LICENSE.upstream`)
- Files: only the `.tf` files — unmodified. The five `data.archive_file` blocks point at function source directories outside this fixture (`../<name>/`), deliberately not vendored.
- Purpose: real-world Terraform fixture for `tests/test_real_world.py` (parse, graph, run must not crash; warnings must be named).
