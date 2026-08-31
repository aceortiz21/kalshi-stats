# Automation Infrastructure Canary

This is a bounded automation infrastructure canary. It is not a research task.

Modify only `{{CANARY_TARGET}}`.

Create that file and write exactly this single line, followed by one newline:

`{{EXPECTED_LINE}}`

Do not inspect credentials or authentication files. Do not access `main`.
Do not access `/home/aceortiz/stats` or `~/stats`. Do not make network requests unless
the Codex client itself requires them. Do not make dependency, production,
scientific-evidence, database, runtime, or unrelated code changes. Do not commit.
Stop immediately after the expected output is produced and report completion.
