# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial Streamable HTTP MCP server with `querit_search` and `querit_contents`.
- Anonymous free-tier support with Redis-backed limits and optional BYOK authentication.
- Health endpoints, container build, tests, static analysis, and deployment guidance.

### Changed

- Use a Redis-backed rolling one-second QPS window and fail open when runtime Redis checks fail.
- Require a user-provided Querit API Key for search calls with `include_content=true`.
