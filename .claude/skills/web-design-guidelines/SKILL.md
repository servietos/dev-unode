---
name: web-design-guidelines
description: Review UI code for Web Interface Guidelines compliance. Use when asked to "review my UI", "check accessibility", "audit design", "review UX", or "check my site against best practices".
metadata:
  author: vercel
  version: "1.0.0"
  argument-hint: <file-or-pattern>
---

# Web Interface Guidelines

Review files for compliance with Web Interface Guidelines.

## How It Works

1. Read the rules in `rules.md` next to this file
2. Read the specified files (or prompt user for files/pattern)
3. Check against all rules in `rules.md`
4. Output findings in the terse `file:line` format described there

## Guidelines Source

`rules.md` is a reviewed, pinned copy of
https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md
(commit 4ecfb9fb8d1d, MIT license, copied 2026-11-15). This project uses the local copy instead of
fetching the rules on every run, so the instructions cannot change unnoticed. To update, fetch the
file again, review the diff, and replace `rules.md`.

Rules written for React/Next.js (`onKeyDown`, `useState`, hydration, `nuqs`, Tailwind classes)
apply by analogy to plain HTML/JS: native `<button>` elements already handle the keyboard.

## Usage

When a user provides a file or pattern argument:
1. Read `rules.md`
2. Read the specified files
3. Apply all rules from `rules.md`
4. Output findings using the format specified there

If no files specified, ask the user which files to review.
